"""Crop framing, original-video references and real encoded pixels/timing."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import json
import shutil
import unittest

from PIL import Image, ImageChops, ImageStat
from goprovbox.crop import Crop, rectangle, display_aspect, original_paths, reference_for, positioned, validate_choice
from goprovbox.engine import Scan, dimensions, encode, export, fingerprint, intersections, scan_folder
from goprovbox.gpmf import TelemetryError
from goprovbox.media import executable, preview, probe, run
from goprovbox.overlay import Renderer, parse_scene
from tests.test_core import fixture, video
from tests.test_overlay import four_channel_vbo, scene_members


class CropGeometryTests(unittest.TestCase):
    def test_remove_top_bottom_and_centre_have_unambiguous_meanings(self):
        for mode, y in [('top', 700), ('bottom', 0), ('centre', 350)]:
            with self.subTest(mode=mode):
                self.assertEqual(rectangle(1600, 1600, 0, Fraction(16, 9), mode), Crop(1600, 900, 0, y))
        self.assertIsNone(rectangle(1600, 1600, 0, Fraction(16, 9), 'none'))

    def test_rotate_before_cropping_and_scale_afterwards(self):
        for rotation in (90, 270):
            crop = rectangle(1080, 1920, rotation, Fraction(4, 3), 'centre')
            self.assertEqual(crop, Crop(1440, 1080, 240, 0))
            self.assertEqual(dimensions(video(Path('clip.mp4')), rotation, 960, crop), (960, 720))

    def test_wider_source_crops_sides_equally_for_every_mode(self):
        for mode in ('top', 'bottom', 'centre'):
            self.assertEqual(rectangle(1920, 1080, 0, Fraction(1), mode), Crop(1080, 1080, 420, 0))

    def test_matching_shape_odd_dimensions_and_invalid_choices(self):
        self.assertEqual(rectangle(1920, 1080, 180, Fraction(16, 9), 'top'), Crop(1920, 1080, 0, 0))
        crop = rectangle(5313, 5313, 0, Fraction(16, 9), 'centre')
        self.assertTrue(all(x % 2 == 0 for x in (crop.width, crop.height, crop.x, crop.y)))
        self.assertLess(abs(crop.width / crop.height - 16 / 9), .001)
        self.assertLessEqual(crop.y + crop.height, 5313)
        with self.assertRaises(TelemetryError): rectangle(100, 100, 0, Fraction(1), 'bad')

    def test_reference_uses_display_shape_including_non_square_pixels_and_rotation(self):
        meta = {'streams': [{'codec_type': 'audio'}, {'codec_type': 'video', 'width': 720, 'height': 576, 'sample_aspect_ratio': '64:45'}]}
        self.assertEqual(display_aspect(meta), Fraction(16, 9))
        meta['streams'][1]['side_data_list'] = [{'rotation': -90}]
        self.assertEqual(display_aspect(meta), Fraction(9, 16))
        meta['streams'][1]['sample_aspect_ratio'] = '0:1'
        self.assertEqual(display_aspect(meta), Fraction(4, 5))
        with self.assertRaises(TelemetryError): display_aspect({'streams': []})

    def test_custom_position_clamps_to_frame_and_roundtrips_source_pixels(self):
        for rotation in (0, 90, 180, 270):
            for width, height in ((1600, 1600), (1080, 1920), (5313, 3101)):
                for x, y in ((-100, -100), (121, 238), (99999, 99999)):
                    choice = positioned(width, height, rotation, Fraction(16, 9), x, y)
                    crop = rectangle(width, height, rotation, Fraction(16, 9), choice)
                    w, h = (height, width) if rotation % 180 else (width, height)
                    self.assertGreaterEqual(crop.x, 0); self.assertGreaterEqual(crop.y, 0)
                    self.assertLessEqual(crop.x + crop.width, w); self.assertLessEqual(crop.y + crop.height, h)
                    self.assertTrue(all(n % 2 == 0 for n in (crop.x, crop.y, crop.width, crop.height)))
                    again = positioned(width, height, rotation, Fraction(16, 9), crop.x, crop.y)
                    self.assertEqual(rectangle(width, height, rotation, Fraction(16, 9), again), crop)
        self.assertEqual(rectangle(1600, 1600, 0, Fraction(16, 9),
                                  positioned(1600, 1600, 0, Fraction(16, 9), 0, 238)), Crop(1600, 900, 0, 238))
        self.assertEqual(rectangle(1920, 1080, 0, Fraction(1),
                                  positioned(1920, 1080, 0, Fraction(1), 120, 0)), Crop(1080, 1080, 120, 0))

    def test_reject_invalid_or_nonfinite_custom_coordinates(self):
        for value in (None, [], 'custom', {'mode': 'custom'}, {'mode': 'centre', 'x': .5, 'y': .5}):
            with self.subTest(value=value), self.assertRaises(TelemetryError): validate_choice(value)
        for x in (True, '0.5', -.01, 1.01, float('nan'), float('inf')):
            with self.subTest(x=x), self.assertRaises(TelemetryError):
                validate_choice({'mode': 'custom', 'x': x, 'y': .5})


class CropReferenceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.vbo = fixture(self.folder / 'data.vbo')
        self.clip = video(self.folder / 'gopro.mp4')
        self.matches = intersections(self.vbo, self.clip)
        self.original = self.folder / 'vbox0001_0001.MP4'
        self.original.write_bytes(b'original')

    def test_header_names_case_insensitive_and_only_overlapping_chapters(self):
        # Filename comes from [AVI], not the VBO filename. A missing chapter
        # outside this GoPro's overlap must not disable the crop.
        self.vbo.rows[-1].values[self.vbo.index('avifileindex')] = '0002'
        self.assertEqual(original_paths(self.matches), [self.original])

    def test_missing_reference_does_not_search_subfolders(self):
        sub = self.folder / 'sub'; sub.mkdir(); self.original.rename(sub / self.original.name)
        with self.assertRaisesRegex(TelemetryError, 'Keep VBOX0001_0001.mp4'):
            original_paths(self.matches)

    def test_mixed_reference_shapes_refuse_guessing_and_metadata_is_cached(self):
        self.matches[0].rows[-1].values[self.vbo.index('avifileindex')] = '0002'
        second = self.folder / 'VBOX0001_0002.mp4'; second.write_bytes(b'second')
        def meta(path):
            return {'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080 if path == self.original else 1440}]}
        with patch('goprovbox.crop.probe', side_effect=meta) as mocked:
            cache = {}
            for _ in range(2):
                with self.assertRaisesRegex(TelemetryError, 'different shapes'): reference_for(self.matches, cache)
            self.assertEqual(mocked.call_count, 2)

    def test_reference_rejects_folder_paths(self):
        self.vbo.preamble[self.vbo.preamble.index('[AVI]') + 1] = '../VBOX0001_'
        with self.assertRaisesRegex(TelemetryError, 'recordings folder'): original_paths(self.matches)

    def test_optional_reference_failure_keeps_scan_and_uncropped_export_available(self):
        self.original.unlink(); self.clip.path.write_bytes(b'gopro')
        with patch('goprovbox.engine.inspect_video', return_value=self.clip), patch('goprovbox.engine.probe', return_value={'streams': [{'codec_tag_string': 'gpmd'}]}):
            result = scan_folder(self.folder)
        self.assertEqual(result.errors, [])
        self.assertTrue(result.matches)
        self.assertIn(self.clip.path.name, result.crop_errors)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class CropEncodingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.source = self.folder / 'GX010001.mp4'
        # Four horizontal bands identify exactly which end was removed.
        run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
             'color=white:s=160x160:r=30,drawbox=x=0:y=0:w=160:h=40:color=red:t=fill,drawbox=x=0:y=40:w=160:h=40:color=green:t=fill,drawbox=x=0:y=80:w=160:h=40:color=blue:t=fill',
             '-f', 'lavfi', '-i', 'sine=frequency=500:sample_rate=48000', '-t', '3', '-c:v', 'libx264', '-c:a', 'aac', str(self.source)])
        self.original = self.folder / 'VBOX0001_0001.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=160x80:r=30', '-t', '3', '-c:v', 'libx264', str(self.original)])
        self.vbo = fixture(self.folder / 'VBOX0001.vbo')
        self.clip = replace(video(self.source, duration=3), width=160, height=160)
        self.matches = intersections(self.vbo, self.clip)
        self.scan = Scan(self.folder, [self.clip], [self.vbo], self.matches, [], [],
                         {p.name: fingerprint(p) for p in (self.source, self.vbo.path)},
                         {self.source.name: reference_for(self.matches)})

    def test_all_positions_preview_pixels_audio_telemetry_and_safe_reruns(self):
        expected = {'top': [(0, 0, 255), (255, 255, 255)], 'bottom': [(255, 0, 0), (0, 128, 0)], 'centre': [(0, 128, 0), (0, 0, 255)]}
        for mode, colours in expected.items():
            with self.subTest(mode=mode):
                result = export(self.scan, self.folder / mode, crops={self.source.name: mode}, encoder='software')
                report = json.loads((result / 'report.json').read_text(encoding='utf-8'))
                media = report['media'][0]; movie = result / media['file']
                self.assertEqual(media['verification']['dimensions'], [160, 80])
                self.assertEqual(media['verification']['frames'], '90')
                self.assertTrue(report['outputs'][0]['telemetry_preserved'])
                self.assertTrue(any(s['codec_type'] == 'audio' for s in probe(movie)['streams']))
                before = self.folder / 'before.png'; after = self.folder / 'after.png'
                preview(self.source, 1, 0, before, crop=rectangle(160, 160, 0, Fraction(2), mode), output_size=(160, 80))
                preview(movie, 1, 0, after, output_size=(160, 80))
                with Image.open(before) as a, Image.open(after) as b:
                    self.assertLess(max(ImageStat.Stat(ImageChops.difference(a.convert('RGB'), b.convert('RGB'))).mean), 4)
                    for y, colour in zip((20, 60), colours):
                        self.assertLess(max(abs(a - b) for a, b in zip(b.getpixel((80, y))[:3], colour)), 12)
                self.assertEqual(export(self.scan, result, crops={self.source.name: mode}, encoder='software'), result)
                with self.assertRaisesRegex(TelemetryError, 'already exists'):
                    export(self.scan, result, crops={self.source.name: 'none'}, encoder='software')
        self.assertEqual({p.name: fingerprint(p) for p in (self.source, self.vbo.path)}, self.scan.fingerprints)

    def test_rotated_crop_overlay_stays_inside_frame_and_matches_preview(self):
        vbo = four_channel_vbo(self.folder)
        clip = replace(self.clip, orientation=replace(self.clip.orientation, clockwise=90))
        matches = intersections(vbo, clip)
        crop = rectangle(160, 160, 90, Fraction(2), 'top')
        scene = parse_scene(scene_members())
        renderer = Renderer(clip, matches, 160, 80, scene)
        self.assertLessEqual(renderer.position[0] + renderer.size[0], 160)
        self.assertLessEqual(renderer.position[1] + renderer.size[1], 80)
        dest = self.folder / 'overlay.mp4'
        encode(clip, dest, 90, 0, 'software', lambda m: None, lambda p: None, Event(), renderer, crop)
        before = self.folder / 'rotated.png'; after = self.folder / 'encoded.png'
        preview(clip.path, 1, 90, before, crop=crop, output_size=(160, 80))
        preview(dest, 1, 0, after, output_size=(160, 80))
        with Image.open(before) as im, Image.open(after) as encoded:
            expected = im.convert('RGBA'); expected.alpha_composite(renderer.frame(1), renderer.position)
            error = ImageStat.Stat(ImageChops.difference(expected.convert('RGB'), encoded.convert('RGB'))).mean
            self.assertLess(max(error), 10)
            # Upright video is white at the left and red at the right after 90°.
            self.assertGreater(encoded.getpixel((10, 65))[1], 230)
            self.assertGreater(encoded.getpixel((150, 65))[0], 230)
            self.assertLess(encoded.getpixel((150, 65))[1], 20)

    def test_changed_reference_and_missing_reference_fail_before_encoding(self):
        with self.original.open('ab') as file: file.write(b'changed')
        with patch('goprovbox.engine.encode') as mocked:
            with self.assertRaisesRegex(TelemetryError, 'changed since scan'):
                export(self.scan, self.folder / 'out', crops={self.source.name: 'top'})
            self.original.unlink()
            with self.assertRaisesRegex(TelemetryError, 'Keep VBOX'):
                export(self.scan, self.folder / 'out', crops={self.source.name: 'centre'})
            mocked.assert_not_called()
        self.assertFalse((self.folder / 'out').exists())

    def test_custom_editor_output_matches_encoded_crop_gauges_sound_and_rerun(self):
        from goprovbox.framing import prepare_preview
        from goprovbox.vbo import write_vbo, read_vbo
        self.vbo = four_channel_vbo(self.folder)
        write_vbo(self.vbo, self.vbo.rows, list(range(len(self.vbo.rows))), 'VBOX0001_', self.vbo.path)
        self.vbo = read_vbo(self.vbo.path)
        self.matches = intersections(self.vbo, self.clip)
        self.scan.vbos = [self.vbo]; self.scan.matches = self.matches
        self.scan.fingerprints = {p.name: fingerprint(p) for p in (self.source, self.vbo.path)}
        choice = positioned(160, 160, 0, Fraction(2), 0, 22)
        prepared = prepare_preview(self.clip, self.matches, 0, 160, Fraction(2), 'four', '',
                                   self.folder / 'source.png', Event())
        shown = prepared.output(choice, (160, 80))
        result = export(self.scan, self.folder / 'custom', crops={self.source.name: choice},
                        rotations={self.source.name: 0}, encoder='software', telemetry_overlay=True)
        report = json.loads((result / 'report.json').read_text(encoding='utf-8'))
        media = report['media'][0]; movie = result / media['file']
        self.assertEqual(report['settings']['crops'][self.source.name]['rectangle'],
                         {'width': 160, 'height': 80, 'x': 0, 'y': 22})
        self.assertTrue(report['outputs'][0]['telemetry_preserved'])
        self.assertTrue(any(s['codec_type'] == 'audio' for s in probe(movie)['streams']))
        preview(movie, prepared.seconds, 0, self.folder / 'exported.png', output_size=(160, 80))
        with Image.open(self.folder / 'exported.png') as encoded:
            self.assertLess(max(ImageStat.Stat(ImageChops.difference(shown.convert('RGB'), encoded.convert('RGB'))).mean), 10)
        self.assertEqual(export(self.scan, result, crops={self.source.name: choice},
                                rotations={self.source.name: 0}, encoder='software', telemetry_overlay=True), result)
        with self.assertRaisesRegex(TelemetryError, 'already exists'):
            export(self.scan, result, crops={self.source.name: dict(choice, y=.6)}, encoder='software', telemetry_overlay=True)


if __name__ == '__main__':
    unittest.main()
