"""Trimming must keep source frames, sound, data links and GPS-clock drift aligned."""
from array import array
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import json
import math
import shutil
import unittest

from PIL import Image, ImageChops, ImageStat
from goprovbox.crop import rectangle
from goprovbox.engine import Scan, Match, export, encode, fingerprint, intersections
from goprovbox.gpmf import TelemetryError
from goprovbox.media import executable, run, probe, preview
from goprovbox.overlay import Renderer, parse_scene
from goprovbox.trimming import video_parts
from goprovbox.vbo import read_vbo
from tests.test_core import fixture, video, BASE
from tests.test_overlay import four_channel_vbo, scene_members


def timestamps(start_ms, stop_ms):
    return [f'1200{ms // 1000:02d}.{ms % 1000:03d}' for ms in range(start_ms, stop_ms + 1, 100)]


class TrimPlanTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)
        self.clip = video(self.folder / 'GX010001.mp4', duration=8)

    def test_default_includes_boundary_frames_and_keeps_full_option(self):
        vbo = fixture(self.folder / 'test.vbo', timestamps(2017, 4117))
        matches = intersections(vbo, self.clip)
        parts = video_parts(self.clip, matches)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].start, 2)
        self.assertEqual(parts[0].frames, 64)
        self.assertAlmostEqual(parts[0].end, 124 / 30)
        self.assertTrue(all(parts[0].contains(row.utc) for row in matches[0].rows))
        full = video_parts(self.clip, matches, False)
        self.assertIs(full[0].video, self.clip)
        self.assertEqual(full[0].video.duration, 8)

    def test_gaps_produce_separate_parts_and_overlapping_runs_merge(self):
        vbo = fixture(self.folder / 'test.vbo', timestamps(1000, 3100) + timestamps(5000, 7100))
        matches = intersections(vbo, self.clip)
        parts = video_parts(self.clip, matches)
        self.assertEqual([p.start for p in parts], [1, 5])
        self.assertFalse(any(p.contains(BASE + 4) for p in parts))
        bridge = fixture(self.folder / 'bridge.vbo', timestamps(3000, 5100))
        combined = video_parts(self.clip, matches + intersections(bridge, self.clip))
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0].start, 1)

    def test_chapter_edge_between_samples_is_retained_when_vbox_continues(self):
        vbo = fixture(self.folder / 'test.vbo', timestamps(0, 8100))
        clip = replace(self.clip, fps='30000/1001', duration=90 * 1001 / 30000,
                       clock=replace(self.clip.clock, origin=BASE + .017))
        part = video_parts(clip, intersections(vbo, clip))[0]
        self.assertEqual(part.start, 0); self.assertEqual(part.frames, 90)
        self.assertAlmostEqual(part.video.duration, clip.duration)

    def test_clock_drift_is_retained_after_trimming(self):
        clip = replace(self.clip, fps='30000/1001', clock=replace(self.clip.clock, rate=1.0005))
        vbo = fixture(self.folder / 'test.vbo', timestamps(2017, 4117))
        part = video_parts(clip, intersections(vbo, clip))[0]
        self.assertEqual(part.video.clock.rate, clip.clock.rate)
        for t in (0, .3, 1.5):
            self.assertAlmostEqual(part.video.clock.utc(t), clip.clock.utc(t + part.start), places=6)
        self.assertAlmostEqual(part.start * float(Fraction(clip.fps)), round(part.start * float(Fraction(clip.fps))))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class TrimEncodingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)
        self.source = self.folder / 'GX010001.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
             "nullsrc=s=160x160:r=30,geq=lum='16+mod(N,7)*30':cb=128:cr=128",
             '-f', 'lavfi', '-i', 'aevalsrc=sin(2*PI*(200*t+40*t*t)):s=48000',
             '-t', '8', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', str(self.source)])
        self.clip = replace(video(self.source, duration=8), width=160, height=160)

    def scan(self, vbo, clips=None):
        clips = clips or [self.clip]
        return Scan(self.folder, clips, [vbo], [m for clip in clips for m in intersections(vbo, clip)], [], [],
                    {p.name:fingerprint(p) for p in [vbo.path] + [c.path for c in clips]})

    def frame_values(self, path):
        return list(run([executable('ffmpeg'), '-v', 'error', '-i', str(path), '-map', '0:v:0',
                         '-vf', 'scale=1:1', '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1']))

    def test_default_export_exact_frames_audio_rebased_vbo_and_opt_out(self):
        vbo = fixture(self.folder / 'test.vbo', timestamps(2017, 4117)); scan = self.scan(vbo)
        result = export(scan, encoder='software')
        self.assertTrue(result.name.endswith(' Matched'))
        report = json.loads((result / 'report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['settings']['overlap_only'])
        media = report['media'][0]; movie = result / media['file']
        self.assertEqual(media['range']['source_start_seconds'], 2)
        self.assertEqual(media['verification']['frames'], '64')
        original, trimmed = self.frame_values(self.source), self.frame_values(movie)
        self.assertEqual(len(trimmed), 64)
        self.assertLess(max(abs(a - b) for a, b in zip(original[60:124], trimmed)), 4)
        output_vbo = read_vbo(result / report['outputs'][0]['file'])
        self.assertEqual(len(output_vbo.rows), len(vbo.rows))
        self.assertEqual(int(output_vbo.rows[0].values[output_vbo.index('avitime')]), 17)
        self.assertTrue(report['outputs'][0]['telemetry_preserved'])
        # Compare decoded sound in the middle, excluding AAC edge transients.
        audio = []
        for path, start in [(self.source, 2.4), (movie, .4)]:
            audio.append(array('f', run([executable('ffmpeg'), '-v', 'error', '-i', str(path), '-ss', str(start),
                                        '-t', '0.4', '-map', '0:a:0', '-f', 'f32le', '-ac', '1', 'pipe:1'])))
        a, b = audio; self.assertEqual(len(a), len(b))
        correlation = sum(x*y for x,y in zip(a,b)) / math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
        self.assertGreater(correlation, .98)
        self.assertEqual(export(scan, result, encoder='software'), result)
        with self.assertRaisesRegex(TelemetryError, 'already exists'):
            export(scan, result, encoder='software', overlap_only=False)
        full = export(scan, self.folder / 'full', encoder='software', overlap_only=False)
        self.assertEqual(probe(next(full.glob('*.mp4')))['streams'][0]['nb_frames'], '240')
        self.assertEqual({p.name:fingerprint(p) for p in (self.source,vbo.path)}, scan.fingerprints)

    def test_gaps_export_two_numbered_clips_with_correct_data_links(self):
        vbo = fixture(self.folder / 'test.vbo', timestamps(1000, 3100) + timestamps(5000, 7100))
        result = export(self.scan(vbo), self.folder / 'gaps', encoder='software')
        report = json.loads((result / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(len(report['media']), 2); self.assertEqual(len(report['outputs']), 2)
        original = self.frame_values(self.source)
        for i, media in enumerate(report['media']):
            self.assertEqual(media['range']['source_start_seconds'], (1,5)[i])
            values = self.frame_values(result / media['file']); first = (30,150)[i]
            self.assertLess(max(abs(a-b) for a,b in zip(original[first:first+len(values)],values)), 4)
        for i, output in enumerate(report['outputs']):
            vbo = read_vbo(result / output['file'])
            self.assertEqual({int(r.values[vbo.index('avifileindex')]) for r in vbo.rows}, {i+1})
            self.assertEqual(int(vbo.rows[0].values[vbo.index('avitime')]), 0)
            self.assertEqual(output['videos'], [report['media'][i]['file']])

    def test_trimmed_rotated_crop_overlay_uses_source_gps_time(self):
        vbo = four_channel_vbo(self.folder)
        clip = replace(self.clip, clock=replace(self.clip.clock, origin=BASE-2), orientation=replace(self.clip.orientation, clockwise=270))
        matches = intersections(vbo, clip); part = video_parts(clip, matches)[0]
        crop = rectangle(160, 160, 270, Fraction(2), 'bottom')
        renderer = Renderer(part.video, matches, 160, 80, parse_scene(scene_members()))
        output = self.folder / 'overlay.mp4'
        encode(part.video, output, 270, 0, 'software', lambda m:None, lambda p:None, Event(), renderer, crop, part.start)
        before, after = self.folder / 'before.png', self.folder / 'after.png'
        preview(clip.path, part.start + 1, 270, before, crop=crop, output_size=(160,80))
        preview(output, 1, 0, after, output_size=(160,80))
        with Image.open(before) as im, Image.open(after) as encoded:
            expected=im.convert('RGBA'); expected.alpha_composite(renderer.frame(1),renderer.position)
            self.assertLess(max(ImageStat.Stat(ImageChops.difference(expected.convert('RGB'), encoded.convert('RGB'))).mean), 10)
        self.assertEqual(renderer.timeline.at(part.video.clock.utc(1))['brake'], 300)

    def test_adjacent_gopro_chapters_share_a_vbo_with_each_clip_rebased(self):
        first, second = self.folder / 'GX010777.mp4', self.folder / 'GX020777.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-i', str(self.source), '-t', '3', '-c:v', 'libx264', '-c:a', 'aac', str(first)])
        shutil.copyfile(first, second)
        clips = [replace(self.clip, path=first, duration=3),
                 replace(self.clip, path=second, duration=3, clock=replace(self.clip.clock, origin=BASE+3))]
        vbo = fixture(self.folder / 'test.vbo', timestamps(900,5100))
        result = export(self.scan(vbo, clips), self.folder / 'chapters', encoder='software')
        report = json.loads((result / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual([m['verification']['frames'] for m in report['media']], ['63','64'])
        self.assertEqual(len(report['outputs']), 1)
        output = read_vbo(result / report['outputs'][0]['file'])
        self.assertEqual([r.utc for r in output.rows], [r.utc for r in vbo.rows])
        for index in (1,2):
            rows = [r for r in output.rows if int(r.values[output.index('avifileindex')]) == index]
            self.assertEqual(int(rows[0].values[output.index('avitime')]), 0)

    def test_nonzero_container_start_and_fractional_fps_keep_the_first_frame(self):
        shifted = self.folder / 'shifted.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-i', str(self.source), '-vf', 'setpts=N/(30000/1001*TB)',
             '-r', '30000/1001', '-c:v', 'libx264', '-c:a', 'aac', '-output_ts_offset', '5', str(shifted)])
        stream=next(s for s in probe(shifted)['streams'] if s['codec_type']=='video')
        clip=replace(self.clip,path=shifted,fps=stream['avg_frame_rate'],video_start=float(stream['start_time']),duration=float(stream['duration']))
        vbo=fixture(self.folder/'test.vbo',timestamps(2017,4117));scan=self.scan(vbo,[clip])
        result=export(scan,self.folder/'offset',encoder='software')
        movie=next(result.glob('*.mp4'));part=video_parts(clip,scan.matches)[0]
        first=round(part.start*float(Fraction(clip.fps)))
        original,trimmed=self.frame_values(shifted),self.frame_values(movie)
        self.assertEqual(len(trimmed),part.frames)
        self.assertLess(max(abs(a-b) for a,b in zip(original[first:first+part.frames],trimmed)),4)


if __name__ == '__main__': unittest.main()
