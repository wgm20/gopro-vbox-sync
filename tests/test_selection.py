"""Checked videos define export scope, independently of the preview selection."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch
import json
import queue
import shutil
import unittest
from PIL import Image
from tests.export_case import ExportTestCase
from goprovbox.reports import export_report_path

from goprovbox.crop import reference_for
from goprovbox.engine import Scan, export, fingerprint, intersections, select_for_export
from goprovbox.gpmf import TelemetryError
from goprovbox.media import executable, preview, probe, run
from tests.test_core import fixture, video


class SelectionTests(ExportTestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.vbo = fixture(self.folder / 'VBOX0001.vbo')
        self.clips = [video(self.folder / name, duration=3) for name in ('GX010001.mp4', 'GX010002.mp4')]
        self.scan = Scan(self.folder, self.clips, [self.vbo],
                         [m for v in self.clips for m in intersections(self.vbo, v)], [], [],
                         {p.name: {'fixture': p.name} for p in [v.path for v in self.clips] + [self.vbo.path]})

    def test_selection_keeps_full_source_history_and_original_scan(self):
        chosen = select_for_export(self.scan, {'GX010002.mp4'})
        self.assertEqual(chosen.videos, [self.clips[1]])
        self.assertTrue(all(m.video is self.clips[1] for m in chosen.matches))
        self.assertIs(chosen.vbos[0], self.vbo)
        self.assertGreater(len(chosen.vbos[0].rows), len(chosen.matches[0].rows))
        self.assertEqual(set(chosen.fingerprints), {'GX010002.mp4', 'VBOX0001.vbo'})
        self.assertEqual(len(self.scan.videos), 2)
        self.assertEqual(len(self.scan.matches), 2)

    def test_empty_unknown_and_unmatched_selections_never_start_export(self):
        unmatched = video(self.folder / 'unmatched.mp4')
        self.scan.videos.append(unmatched)
        for names in (set(), {'missing.mp4'}, {'unmatched.mp4'}):
            with self.subTest(names=names), patch('goprovbox.engine.encode') as encode:
                with self.assertRaises(TelemetryError):
                    export(self.scan, self.folder / 'out', include_videos=names)
                encode.assert_not_called()
                self.assertFalse((self.folder / 'out').exists())

    def test_gui_dispatch_captures_checkboxes_not_the_preview_row(self):
        from goprovbox.gui import App
        self.scan.crop_references = {'GX010002.mp4': Mock()}
        app = self.gui_export_app({'GX010002.mp4'}, 'top')
        App.begin_export(app)
        job = app.worker.call_args.args[0]
        # A pending job owns a snapshot even if UI state is subsequently reset.
        app.included_videos.clear()
        app.crop_choice = 'none'
        app.overlap_only.get.return_value = False
        app.audio_source.get.return_value = 'GoPro'
        with patch('goprovbox.gui.export', return_value=self.folder / 'out') as dispatch:
            job()
        self.assertEqual(dispatch.call_args.kwargs['include_videos'], {'GX010002.mp4'})
        self.assertEqual(dispatch.call_args.kwargs['crops'], {'GX010002.mp4': 'top'})
        self.assertTrue(dispatch.call_args.kwargs['overlap_only'])
        self.assertEqual(dispatch.call_args.kwargs['audio_source'], 'vbox')
        self.assertEqual(dispatch.call_args.kwargs['rotations'], {v.path.name: 0 for v in self.clips})
        app.selected_video.assert_not_called()

    def gui_export_app(self, included, crop_choice):
        from goprovbox.gui import App
        app = App.__new__(App)
        app.scan = self.scan
        app.included_videos = set(included)
        app.output = Mock(); app.output.get.return_value = str(self.folder / 'out')
        app.quality = Mock(); app.quality.get.return_value = 'HD · 1920 px'
        app.overlay_mode = Mock(); app.overlay_mode.get.return_value = 'None'
        app.rotations = {}; app.crop_choice = crop_choice; app.status = Mock(); app.progress = {}; app.cancel = Event()
        app.overlap_only = Mock(); app.overlap_only.get.return_value = True
        app.audio_source = Mock(); app.audio_source.get.return_value = 'VBOX (GoPro for gaps)'
        app.events = queue.Queue(); app.worker = Mock(); app.log = Mock()
        app.selected_video = Mock(return_value=self.clips[0])
        return app

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_gui_shared_custom_crop_reaches_both_encoded_videos(self):
        from goprovbox.gui import App
        for index, clip in enumerate(self.clips):
            side = 160 * (index + 1)
            bands = (f'color=white:s={side}x{side}:r=30,'
                     f'drawbox=x=0:y=0:w={side}:h={side // 4}:color=red:t=fill,'
                     f'drawbox=x=0:y={side // 4}:w={side}:h={side // 4}:color=green:t=fill,'
                     f'drawbox=x=0:y={side // 2}:w={side}:h={side // 4}:color=blue:t=fill')
            run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', bands,
                 '-t', '3', '-c:v', 'libx264', str(clip.path)])
            self.clips[index] = replace(clip, width=side, height=side)
        original = self.folder / 'VBOX0001_0001.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=160x80:r=30',
             '-t', '3', '-c:v', 'libx264', str(original)])
        excluded = video(self.folder / 'unchecked.mp4', duration=3, rotation=None)
        excluded.path.write_bytes(b'unchecked source')
        self.scan.videos = self.clips + [excluded]
        self.scan.matches = [m for clip in self.scan.videos for m in intersections(self.vbo, clip)]
        self.scan.fingerprints = {path.name: fingerprint(path)
                                  for path in [clip.path for clip in self.scan.videos] + [self.vbo.path]}
        self.scan.crop_references = {
            clip.path.name: reference_for([m for m in self.scan.matches if m.video is clip])
            for clip in self.clips}
        excluded.path.unlink()
        included = {clip.path.name for clip in self.clips}
        choice = {'mode': 'custom', 'x': .5, 'y': .25}
        app = self.gui_export_app(included, choice)
        app.selected_video.return_value = excluded
        app.audio_source.get.return_value = 'GoPro'
        App.begin_export(app)
        job = app.worker.call_args.args[0]
        choice['y'] = 1.0
        app.crop_choice = 'none'
        app.included_videos.clear()

        def software_export(*args, **kwargs):
            return export(*args, encoder='software', **kwargs)

        with patch('goprovbox.gui.export', side_effect=software_export) as dispatch:
            job()
        self.assertEqual(dispatch.call_args.kwargs['include_videos'], included)
        self.assertEqual(dispatch.call_args.kwargs['crops'],
                         {name: {'mode': 'custom', 'x': .5, 'y': .25} for name in included})
        app.selected_video.assert_not_called()
        events = []
        while not app.events.empty():
            events.append(app.events.get_nowait())
        result = next(value for kind, value in events if kind == 'complete')
        report = json.loads(export_report_path(result, 'report.json').read_text(encoding='utf-8'))
        self.assertEqual({media['source'] for media in report['media']}, included)
        self.assertEqual(report['excluded_videos'], [excluded.path.name])
        self.assertEqual(set(report['settings']['crops']), included)
        self.assertEqual(len(list(result.glob('*.mp4'))), 2)
        self.assertTrue(report['outputs'])
        self.assertTrue(all(output['telemetry_preserved'] for output in report['outputs']))
        for clip in self.clips:
            with self.subTest(source=clip.path.name):
                media = next(media for media in report['media'] if media['source'] == clip.path.name)
                movie = result / media['file']
                width, height = clip.width, clip.height // 2
                self.assertEqual(media['verification']['dimensions'], [width, height])
                self.assertEqual(media['crop']['rectangle'],
                                 {'width': width, 'height': height, 'x': 0, 'y': clip.height // 8})
                self.assertEqual(media['crop']['position'], {'x': .5, 'y': .25})
                stream = next(stream for stream in probe(movie)['streams'] if stream['codec_type'] == 'video')
                self.assertEqual((stream['width'], stream['height']), (width, height))
                frame = self.folder / (clip.path.stem + '.png')
                preview(movie, 1, 0, frame, output_size=(width, height))
                with Image.open(frame) as image:
                    for y, expected in zip((height // 8, height * 3 // 8, height * 7 // 8),
                                           ((255, 0, 0), (0, 128, 0), (0, 0, 255))):
                        self.assertLess(max(abs(a - b) for a, b in zip(image.getpixel((width // 2, y))[:3], expected)), 18)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_export_skips_unchecked_media_and_selection_changes_rerun(self):
        first, second = self.clips
        run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=120x80:r=30',
             '-t', '3', '-c:v', 'libx264', str(second.path)])
        # This unchecked video is unreadable and has no known rotation. Neither
        # condition may interfere with exporting the other recording.
        first.path.write_bytes(b'unchecked source')
        first.orientation = replace(first.orientation, clockwise=None)
        self.scan.fingerprints = {p.name:fingerprint(p) for p in (first.path, second.path, self.vbo.path)}
        first.path.unlink()  # An excluded source disappearing must not block export.
        chosen = {second.path.name}
        result = export(self.scan, self.folder / 'out', include_videos=chosen,
                        rotations={first.path.name:270}, crops={first.path.name:'top'}, encoder='software')
        report = json.loads(export_report_path(result, "report.json").read_text())
        self.assertEqual([m['source'] for m in report['media']], [second.path.name])
        self.assertEqual(report['settings']['included_videos'], [second.path.name])
        self.assertEqual(report['excluded_videos'], [first.path.name])
        self.assertEqual(len(list(result.glob('*.mp4'))), 1)
        self.assertEqual(len(list(result.glob('*.vbo'))), 1)
        self.assertTrue(report['outputs'][0]['telemetry_preserved'])
        self.assertEqual(export(self.scan, result, include_videos=chosen, encoder='software'), result)
        # Adding another checked source must not reuse a narrower completed export.
        shutil.copyfile(second.path, first.path)
        first.orientation = replace(first.orientation, clockwise=0)
        self.scan.fingerprints[first.path.name] = fingerprint(first.path)
        with self.assertRaisesRegex(TelemetryError, 'already exists'):
            export(self.scan, result, include_videos={first.path.name, second.path.name}, encoder='software')


if __name__ == '__main__':
    unittest.main()
