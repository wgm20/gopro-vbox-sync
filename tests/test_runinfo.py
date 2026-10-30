"""Run choices use complete VBOX laps and camera GPS time, not filenames."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import os
import unittest

from goprovbox.engine import Match, Scan, scan_folder, select_for_export
from goprovbox.runinfo import collect_run_info, summarize_run, video_run_info
from goprovbox.vbo import Row
from tests.test_core import BASE, video
from tests.test_fullscene import circle_vbo


class RunInfoTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / 'fixture'; self.source.mkdir()
        self.vbo = circle_vbo(self.source)
        self.vbo.path = self.folder / 'VBOX0001.vbo'
        # The second complete lap is 18 s; the first takes 20 s. Preserve the
        # fixture's geometry while changing its synthetic sample timestamps.
        self.vbo.rows = [Row(BASE + (t if t <= 25 else 25 + (t - 25) * .9), row.values.copy())
                         for row in self.vbo.rows for t in [row.utc - BASE]]

    def scan(self, clips, vbos=None):
        vbos = vbos or [self.vbo]
        matches = []
        for clip in clips:
            low, high = clip.clock.utc(0), clip.clock.utc(clip.duration)
            for vbo in vbos:
                rows = [row for row in vbo.rows if low <= row.utc < high]
                if len(rows) > 1:
                    matches.append(Match(vbo, clip, rows, 0, 0))
        return Scan(self.folder, clips, vbos, matches, [], [], {},
                    run_info=collect_run_info(vbos))

    def test_fastest_complete_lap_uses_full_run_and_original_utc_bounds(self):
        info = summarize_run(self.vbo)
        self.assertEqual(info['complete_laps'], 2)
        self.assertEqual(len(info['laps']), 2)
        self.assertEqual([lap['number'] for lap in info['laps']], [1, 2])
        self.assertEqual(info['name'], 'VBOX0001.vbo')
        self.assertEqual(info['utc_start'], self.vbo.rows[0].utc)
        self.assertEqual(info['utc_end'], self.vbo.rows[-1].utc)
        self.assertEqual(info['fastest_lap']['number'], 2)
        self.assertAlmostEqual(info['fastest_lap']['duration_seconds'], 18, places=4)
        self.assertAlmostEqual(info['fastest_lap']['utc_start'], BASE + 25, places=4)
        self.assertAlmostEqual(info['fastest_lap']['utc_end'], BASE + 43, places=4)
        self.assertEqual(info['unavailable_reason'], '')

    def test_telemetry_gap_excludes_a_lap_instead_of_fabricating_a_fast_time(self):
        # Remove the middle of the fastest lap. The unbroken first lap survives.
        self.vbo.rows = [row for row in self.vbo.rows if not BASE + 30 < row.utc < BASE + 32]
        info = summarize_run(self.vbo)
        self.assertEqual(info['complete_laps'], 1)
        self.assertEqual(info['fastest_lap']['number'], 1)
        self.assertAlmostEqual(info['fastest_lap']['duration_seconds'], 20, places=4)

    def test_partial_laps_and_missing_gate_have_explanations(self):
        partial = replace(self.vbo, rows=[row for row in self.vbo.rows if BASE + 10 <= row.utc <= BASE + 35])
        info = summarize_run(partial)
        self.assertIsNone(info['fastest_lap'])
        self.assertEqual(info['complete_laps'], 0)
        self.assertIn('No complete lap', info['unavailable_reason'])
        no_gate = replace(self.vbo, preamble=[line for line in self.vbo.preamble if not line.startswith('Start ')])
        info = summarize_run(no_gate)
        self.assertIsNone(info['fastest_lap'])
        self.assertIn('No start/finish line', info['unavailable_reason'])

    def test_missing_lap_channel_does_not_make_the_recording_unusable(self):
        columns = self.vbo.columns.copy()
        columns[self.vbo.index('heading')] = 'other_channel'
        info = summarize_run(replace(self.vbo, columns=columns))
        self.assertIsNone(info['fastest_lap'])
        self.assertIn('heading', info['unavailable_reason'])
        self.assertEqual(info['utc_start'], self.vbo.rows[0].utc)

    def test_fastest_lap_marks_both_chapters_when_it_spans_a_camera_boundary(self):
        clips = [video(self.folder / 'GX010001.MP4', BASE, 34),
                 video(self.folder / 'GX020001.MP4', BASE + 34, 23)]
        scan = self.scan(clips)
        for index, clip in enumerate(clips):
            info = video_run_info(scan, clip)
            self.assertTrue(info['contains_fastest'])
            self.assertGreater(info['overlap_seconds'], 0)
            self.assertLessEqual(info['overlap_seconds'], clip.duration)
            self.assertEqual(info['runs'][0]['fastest_lap_videos'], [c.path.name for c in clips])
            self.assertAlmostEqual(info['runs'][0]['fastest_lap']['duration_seconds'], 18, places=4)
            if index == 0:
                self.assertAlmostEqual(info['fastest_lap']['duration_seconds'], 20, places=4)
                self.assertEqual(info['complete_laps'], 1)
                self.assertEqual(info['fastest_run'], self.vbo.path.name)
            else:
                self.assertIsNone(info['fastest_lap'])
                self.assertEqual(info['complete_laps'], 0)
                self.assertIn('No complete lap', info['unavailable_reason'])

    def test_file_without_complete_lap_does_not_repeat_a_best_from_another_chapter(self):
        early = video(self.folder / 'GX010001.MP4', BASE, 20)
        later = video(self.folder / 'GX020001.MP4', BASE + 20, 37)
        info = video_run_info(self.scan([early, later]), early)
        self.assertIsNone(info['fastest_lap'])
        self.assertEqual(info['complete_laps'], 0)
        self.assertAlmostEqual(info['runs'][0]['fastest_lap']['duration_seconds'], 18, places=4)
        self.assertFalse(info['contains_fastest'])
        self.assertEqual(info['runs'][0]['fastest_lap_videos'], [later.path.name])
        info = video_run_info(self.scan([early]), early)
        self.assertEqual(info['runs'][0]['fastest_lap_videos'], [])
        self.assertFalse(info['contains_fastest'])

    def test_each_file_has_its_own_best_and_exact_camera_endpoints_count(self):
        clips = [video(self.folder / 'first.MP4', BASE, 25),
                 video(self.folder / 'second.MP4', BASE + 25, 18)]
        scan = self.scan(clips)
        first, second = [video_run_info(scan, clip) for clip in clips]
        self.assertAlmostEqual(first['fastest_lap']['duration_seconds'], 20, places=4)
        self.assertAlmostEqual(second['fastest_lap']['duration_seconds'], 18, places=4)
        self.assertEqual([first['complete_laps'], second['complete_laps']], [1, 1])
        self.assertFalse(first['contains_fastest'])
        self.assertTrue(second['contains_fastest'])

    def test_interpolated_crossings_at_camera_edges_allow_inset_matching_samples(self):
        line = next(i for i, value in enumerate(self.vbo.preamble) if value.startswith('Start '))
        parts = self.vbo.preamble[line].split()
        # Move the gate 5 cm from an exactly sampled crossing. The gate times
        # now lie between samples, with edge context outside the camera file.
        for index in (2, 4):
            parts[index] = str(float(parts[index]) + .05 / 111195.0802 * 60)
        self.vbo.preamble[line] = ' '.join(parts)
        lap = summarize_run(self.vbo)['fastest_lap']
        clip = video(self.folder / 'exact fractional lap.MP4', lap['utc_start'], lap['duration_seconds'])
        scan = self.scan([clip])
        matched = scan.matches[0].rows
        self.assertGreater(matched[0].utc, lap['utc_start'])
        self.assertLess(matched[-1].utc, lap['utc_end'])
        info = video_run_info(scan, clip)
        self.assertEqual(info['complete_laps'], 1)
        self.assertAlmostEqual(info['fastest_lap']['duration_seconds'], lap['duration_seconds'], places=6)

    def test_partial_best_lap_is_not_accepted_when_clock_misses_its_end(self):
        clip = video(self.folder / 'partial.MP4', BASE + 25, 17.9)
        info = video_run_info(self.scan([clip]), clip)
        self.assertIsNone(info['fastest_lap'])
        self.assertEqual(info['complete_laps'], 0)
        self.assertTrue(info['contains_fastest'])

    def test_missing_matching_coverage_excludes_otherwise_contained_lap(self):
        clip = video(self.folder / 'whole-run.MP4', BASE, 57)
        scan = self.scan([clip])
        match = scan.matches[0]
        match.rows = [row for row in match.rows if not BASE + 30 < row.utc < BASE + 31]
        info = video_run_info(scan, clip)
        self.assertEqual(info['complete_laps'], 1)
        self.assertAlmostEqual(info['fastest_lap']['duration_seconds'], 20, places=4)
        self.assertTrue(info['contains_fastest'])

    def test_multiple_linked_runs_keep_individual_results_and_choose_shortest_cell_time(self):
        second_folder = self.folder / 'second fixture'; second_folder.mkdir()
        other = circle_vbo(second_folder)
        other.path = self.folder / 'VBOX0002.vbo'
        other.rows = [Row(BASE + 60 + (row.utc - BASE) * .6, row.values.copy()) for row in other.rows]
        clip = video(self.folder / 'GX010001.MP4', BASE, 120)
        info = video_run_info(self.scan([clip], [self.vbo, other]), clip)
        self.assertEqual(info['run_names'], ['VBOX0001.vbo', 'VBOX0002.vbo'])
        self.assertEqual(info['fastest_run'], 'VBOX0002.vbo')
        self.assertAlmostEqual(info['fastest_lap']['duration_seconds'], 12, places=4)
        self.assertEqual(len(info['runs']), 2)
        self.assertAlmostEqual(info['runs'][0]['fastest_lap']['duration_seconds'], 18, places=4)
        self.assertTrue(info['contains_fastest'])

    def test_unmatched_video_and_legacy_scan_keep_gps_start_without_disk_time(self):
        path = self.folder / 'a misleading filename 1999.MP4'
        path.write_bytes(b'synthetic source'); os.utime(path, (0, 0))
        clip = video(path, BASE + 86400, 5)
        scan = self.scan([clip])
        info = video_run_info(scan, clip)
        self.assertEqual(info['utc_start'], BASE + 86400)
        self.assertEqual(info['utc_end'], BASE + 86405)
        self.assertEqual(info['runs'], [])
        self.assertEqual(info['overlap_seconds'], 0)
        self.assertIsNone(info['fastest_lap'])
        clip = video(path, BASE, 10)
        legacy = self.scan([clip]); legacy.run_info.clear()
        info = video_run_info(legacy, clip)
        self.assertIsNone(info['fastest_lap'])
        self.assertIn('not calculated', info['unavailable_reason'])
        legacy = self.scan([video(path, BASE, 57)])
        del legacy.run_info[self.vbo.path.name]['laps']
        info = video_run_info(legacy, legacy.videos[0])
        self.assertIsNone(info['fastest_lap'])
        self.assertIsNotNone(info['runs'][0]['fastest_lap'])
        self.assertIn('not calculated', info['unavailable_reason'])

    def test_export_selection_keeps_complete_source_summary_but_filters_other_runs(self):
        other_folder = self.folder / 'other fixture'; other_folder.mkdir()
        other = circle_vbo(other_folder); other.path = self.folder / 'VBOX0002.vbo'
        other.rows = [Row(row.utc + 100, row.values.copy()) for row in other.rows]
        first, second = video(self.folder / 'first.MP4', BASE, 15), video(self.folder / 'second.MP4', BASE + 100, 55)
        scan = self.scan([first, second], [self.vbo, other])
        chosen = select_for_export(scan, {first.path.name})
        self.assertEqual(set(chosen.run_info), {'VBOX0001.vbo'})
        self.assertEqual(set(scan.run_info), {'VBOX0001.vbo', 'VBOX0002.vbo'})
        self.assertIs(chosen.vbos[0], self.vbo)
        self.assertEqual(chosen.run_info['VBOX0001.vbo'], scan.run_info['VBOX0001.vbo'])
        self.assertIn('fastest_lap', chosen.summary()['run_info']['VBOX0001.vbo'])

    def test_scan_calculates_laps_once_per_run_for_many_camera_chapters(self):
        self.vbo.path.write_bytes(b'synthetic data placeholder')
        for number in range(8):
            (self.folder / f'GX01{number:04d}.MP4').write_bytes(b'synthetic camera placeholder')
        with patch('goprovbox.engine.executable', return_value='tool'), \
                patch('goprovbox.engine.read_vbo', return_value=self.vbo), \
                patch('goprovbox.engine.probe', return_value={'streams': [{'codec_tag_string': 'gpmd'}]}), \
                patch('goprovbox.engine.inspect_video', side_effect=lambda path, *_: video(path, BASE, 50)), \
                patch('goprovbox.engine.check_pair', return_value=(0, 0, [])), \
                patch('goprovbox.runinfo.summarize_run', wraps=summarize_run) as calculate:
            scan = scan_folder(self.folder)
        calculate.assert_called_once_with(self.vbo)
        self.assertEqual(len(scan.videos), 8)
        self.assertAlmostEqual(scan.run_info[self.vbo.path.name]['fastest_lap']['duration_seconds'], 18, places=4)

    def test_run_calculation_respects_cancellation_before_and_after_a_run(self):
        cancel = Event(); cancel.set()
        with self.assertRaises(InterruptedError), patch('goprovbox.runinfo.summarize_run') as calculate:
            collect_run_info([self.vbo], cancel=cancel)
        calculate.assert_not_called()
        cancel.clear()
        def finish_run(vbo):
            cancel.set()
            return {'name': vbo.path.name}
        with patch('goprovbox.runinfo.summarize_run', side_effect=finish_run):
            with self.assertRaises(InterruptedError):
                collect_run_info([self.vbo], cancel=cancel)


if __name__ == '__main__':
    unittest.main()
