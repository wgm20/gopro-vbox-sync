"""Checked videos define export scope, independently of the preview selection."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import json
import queue
import shutil
import unittest

from goprovbox.engine import Scan, export, fingerprint, intersections, select_for_export
from goprovbox.gpmf import TelemetryError
from goprovbox.media import executable, run
from tests.test_core import fixture, video


class SelectionTests(unittest.TestCase):
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
        app = App.__new__(App)
        app.scan = self.scan
        app.included_videos = {'GX010002.mp4'}
        app.output = Mock(); app.output.get.return_value = str(self.folder / 'out')
        app.quality = Mock(); app.quality.get.return_value = 'HD · 1920 px'
        app.overlay_mode = Mock(); app.overlay_mode.get.return_value = 'None'
        app.rotations = {}; app.crops = {'GX010002.mp4': 'top'}; app.status = Mock(); app.progress = {}; app.cancel = Mock()
        app.overlap_only = Mock(); app.overlap_only.get.return_value = True
        app.events = queue.Queue(); app.worker = Mock()
        app.selected_video = Mock(return_value=self.clips[0])
        App.begin_export(app)
        job = app.worker.call_args.args[0]
        # A pending job owns a snapshot even if UI state is subsequently reset.
        app.included_videos.clear()
        app.crops.clear()
        app.overlap_only.get.return_value = False
        with patch('goprovbox.gui.export', return_value=self.folder / 'out') as dispatch:
            job()
        self.assertEqual(dispatch.call_args.kwargs['include_videos'], {'GX010002.mp4'})
        self.assertEqual(dispatch.call_args.kwargs['crops'], {'GX010002.mp4': 'top'})
        self.assertTrue(dispatch.call_args.kwargs['overlap_only'])
        self.assertEqual(dispatch.call_args.kwargs['rotations'], {v.path.name: 0 for v in self.clips})
        app.selected_video.assert_not_called()

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
        report = json.loads((result / 'report.json').read_text())
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
