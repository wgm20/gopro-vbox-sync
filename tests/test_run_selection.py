"""Selection dates, lap markers and sorting help choose work before encoding."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import tkinter as tk
import unittest
from PIL import Image

from goprovbox.engine import Scan, Match
from goprovbox.gui import App
from goprovbox.runinfo import collect_run_info
from goprovbox.selection import date_and_time, lap_time, moment, recording_details
from goprovbox.vbo import Row
from tests.test_core import BASE, video
from tests.test_fullscene import circle_vbo


class SelectionFormatTests(unittest.TestCase):
    def test_dates_cross_midnight_with_the_selected_offset(self):
        stamp = datetime(2026, 10, 2, 23, 45, tzinfo=timezone.utc).timestamp()
        bst = timezone(timedelta(hours=1))
        self.assertEqual(date_and_time(stamp, 'UTC'), ('2026-10-02', '23:45:00'))
        self.assertEqual(date_and_time(stamp, 'Local time', bst), ('2026-10-03', '00:45:00'))
        self.assertEqual(moment(stamp, 'Local time', bst), '2026-10-03 00:45:00 (UTC+01:00)')
        self.assertEqual(moment(stamp, 'UTC'), '2026-10-02 23:45:00 (UTC)')

    def test_lap_rounding_carries_into_next_minute_and_unknown_is_not_zero(self):
        self.assertEqual(lap_time(79.3346), '1:19.335')
        self.assertEqual(lap_time(59.9998), '1:00.000')
        for value in (None, 0, -1, float('nan'), float('inf')):
            self.assertEqual(lap_time(value), '—')


class RunSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        (self.folder / 'settings.json').write_text(json.dumps({'welcomed': True, 'time_basis': 'UTC'}))
        settings = patch('goprovbox.gui.data_directory', return_value=self.folder)
        settings.start(); self.addCleanup(settings.stop)
        self.root = tk.Tk(); self.root.withdraw()
        self.addCleanup(self.cleanup_root)
        self.app = App(self.root)
        self.addCleanup(self.app.temporary.cleanup)
        self.app.load_preview = lambda *args: None
        self.vbo = circle_vbo(self.folder)
        # Two complete laps take 20 s and 18 s. Each full lap lies in a
        # different file; the final clip contains no complete lap.
        self.vbo.rows = [Row(BASE + (t if t <= 25 else 25 + (t - 25) * .9), row.values.copy())
                         for row in self.vbo.rows for t in [row.utc - BASE]]
        self.clips = [video(self.folder / 'later.MP4', BASE + 45, 15),
                      video(self.folder / 'first.MP4', BASE - 10, 35),
                      video(self.folder / 'second.MP4', BASE + 25, 18)]
        self.assign_scan(self.clips)

    def assign_scan(self, clips):
        self.clips = clips
        self.app.tree.delete(*self.app.tree.get_children())
        matches = [Match(self.vbo, clip,
                         [row for row in self.vbo.rows if clip.clock.utc(0) <= row.utc < clip.clock.utc(clip.duration)],
                         0, 0) for clip in self.clips]
        self.app.scan = Scan(self.folder, self.clips, [self.vbo], matches, [], [], {},
                             run_info=collect_run_info([self.vbo]))
        self.app.included_videos = {clip.path.name for clip in self.clips}
        self.app.refresh_rows()

    def cleanup_root(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()

    def test_default_rows_are_chronological_and_use_camera_start_not_vbox_start(self):
        self.assertEqual(self.app.tree.get_children(), ('1', '2', '0'))
        self.assertEqual(self.app.tree.set('1', 'date'), '2026-07-10')
        self.assertEqual(self.app.tree.set('1', 'start'), '11:59:50')
        self.assertEqual(self.app.tree.set('2', 'start'), '12:00:25')
        self.app.tree.selection_set('1')
        self.assertIs(self.app.selected_video(), self.clips[1])

    def test_best_in_file_uses_only_its_complete_laps_and_star_is_separate(self):
        self.assertEqual(self.app.tree.heading('lap', 'text'), 'Best in file')
        self.assertEqual(self.app.tree.set('1', 'lap'), '0:20.000')
        self.assertEqual(self.app.tree.set('2', 'lap'), '0:18.000')
        self.assertEqual(self.app.tree.set('0', 'lap'), '—')
        self.assertFalse(self.app.tree.item('1', 'image'))
        self.assertTrue(self.app.tree.item('2', 'image'))
        self.assertFalse(self.app.tree.item('0', 'image'))
        marker = self.app.tree.item('2', 'image')[0]
        width = self.root.tk.call('image', 'width', marker)
        height = self.root.tk.call('image', 'height', marker)
        red, green, blue = self.root.tk.call(marker, 'get', width // 2, height // 2)
        self.assertGreater(red, green * 2)
        self.assertGreater(red, blue * 2)
        details = recording_details('first.MP4', self.app.selection_data['1'], 'UTC')
        self.assertIn('Best in file: 0:20.000', details)
        self.assertIn('Whole-run best: 0:18.000', details)
        self.assertIn('second.MP4', details)
        self.assertIn('2026-07-10 11:59:50 (UTC)', details)

    def test_split_run_best_marks_both_parts_without_counting_it_as_complete_in_either(self):
        self.assign_scan([video(self.folder / 'first.MP4', BASE, 34),
                          video(self.folder / 'second.MP4', BASE + 34, 23)])
        self.assertEqual(self.app.tree.set('0', 'lap'), '0:20.000')
        self.assertEqual(self.app.tree.set('1', 'lap'), '—')
        self.assertTrue(self.app.tree.item('0', 'image'))
        self.assertTrue(self.app.tree.item('1', 'image'))
        for index, clip in enumerate(self.clips):
            info = self.app.selection_data[str(index)]
            self.assertTrue(info['contains_fastest'])
            details = recording_details(clip.path.name, info, 'UTC')
            self.assertIn('first.MP4, second.MP4', details)
            self.assertIn('Whole-run best: 0:18.000', details)
            self.assertIn('This video contains only part of that lap.', details)
            if index == 1:
                self.assertIn('No complete lap in this file:', details)

    def test_time_display_and_sorting_preserve_includes_without_scanning_or_encoding(self):
        original = set(self.app.included_videos)
        self.app.tree.selection_set('2')
        original_markers = {row: self.app.tree.item(row, 'image') for row in self.app.tree.get_children()}
        with patch('goprovbox.gui.scan_folder') as scan, patch('goprovbox.gui.export') as encode, \
                patch.object(self.app, 'worker') as worker:
            self.app.time_basis.set('Local time')
            self.app.sort_rows('video')
            self.app.time_basis.set('UTC')
            scan.assert_not_called(); encode.assert_not_called(); worker.assert_not_called()
        self.assertEqual(self.app.included_videos, original)
        self.assertEqual(self.app.tree.selection(), ('2',))
        self.assertEqual({row: self.app.tree.item(row, 'image') for row in self.app.tree.get_children()},
                         original_markers)
        self.assertEqual(self.app.tree.set('1', 'include'), '☑')
        self.app.toggle_include('2')
        self.assertNotIn('second.MP4', self.app.included_videos)
        self.assertIn('first.MP4', self.app.included_videos)

    def test_fastest_sort_is_numeric_and_unavailable_is_last_in_both_directions(self):
        self.app.selection_data['0']['fastest_lap'] = {'duration_seconds': 70}
        self.app.selection_data['1']['fastest_lap'] = {'duration_seconds': 80}
        self.app.selection_data['2']['fastest_lap'] = None
        self.app.sort_rows('lap', False)
        self.assertEqual(self.app.tree.get_children(), ('0', '1', '2'))
        self.app.sort_rows('lap', True)
        self.assertEqual(self.app.tree.get_children(), ('1', '0', '2'))

    def test_table_is_scrollable_at_minimum_size_and_new_controls_fit(self):
        self.root.deiconify(); self.root.geometry('880x740'); self.root.update()
        self.assertTrue(self.app.tree.cget('xscrollcommand'))
        self.assertTrue(self.app.tree.cget('yscrollcommand'))
        self.assertGreaterEqual(self.app.tree.winfo_height(), 150)
        for control in (self.app.time_combo, self.app.create, self.app.open_result):
            self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height())

    def test_full_scene_and_a_photo_keep_export_actions_visible(self):
        self.root.deiconify(); self.root.geometry('1320x860')
        self.app.overlay_mode.set('Full scene · no rear camera'); self.app.overlay_changed()
        self.app.tree.selection_set('1'); self.app.select_video()
        self.root.update()
        self.app.preview_source = Image.new('RGB', (360, 240))
        self.app.resize_preview(); self.root.update()
        for width, height in ((1320, 860), (880, 740)):
            self.root.geometry(f'{width}x{height}'); self.root.update()
            for control in (self.app.create, self.app.open_result, self.app.time_combo):
                self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                     self.root.winfo_rooty() + self.root.winfo_height())
            self.assertLessEqual(self.app.preview_image.width(), self.app.preview_label.winfo_width())
            self.assertLessEqual(self.app.preview_image.height(), self.app.preview_label.winfo_height())


if __name__ == '__main__':
    unittest.main()
