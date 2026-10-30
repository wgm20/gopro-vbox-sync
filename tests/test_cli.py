from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from goprovbox.__main__ import main
from goprovbox.engine import Scan
from tests.test_core import video


class CLITests(unittest.TestCase):
    def test_separate_gopro_folder_recurses_by_default(self):
        scan = Scan(Path('runs'), [], [], [], [], [], {})
        for options, folder, recursive in [
            ([], None, False),
            (['--gopro-folder', 'card'], Path('card'), True),
            (['--gopro-folder', 'card', '--no-gopro-recursive'], Path('card'), False),
            (['--gopro-recursive'], None, True),
        ]:
            with self.subTest(options=options), patch('goprovbox.__main__.scan_folder', return_value=scan) as dispatch:
                self.assertEqual(main(['runs', '--scan'] + options), 2)
            self.assertEqual(dispatch.call_args.kwargs['gopro_folder'], folder)
            self.assertEqual(dispatch.call_args.kwargs['recursive_gopro'], recursive)

    def test_gui_receives_explicit_source_options_and_keeps_unspecified_preferences(self):
        for options, folder, recursive in [
            ([], None, None),
            (['--gopro-folder', 'card'], Path('card'), None),
            (['--gopro-folder', 'card', '--no-gopro-recursive'], Path('card'), False),
        ]:
            with self.subTest(options=options), patch('goprovbox.gui.launch') as launch:
                self.assertEqual(main(['runs', '--gui'] + options), 0)
            launch.assert_called_once_with(Path('runs'), gopro_folder=folder, recursive_gopro=recursive)

    def test_gui_can_start_with_an_explicit_saved_scene(self):
        with patch('goprovbox.gui.launch') as launch:
            self.assertEqual(main(['runs', '--gui', '--scene', 'saved.gvscene']), 0)
        launch.assert_called_once_with(Path('runs'), gopro_folder=None, recursive_gopro=None,
                                       scene_path=Path('saved.gvscene'))

    def test_overlap_only_is_default_and_full_video_opts_out(self):
        scan = Scan(Path('.'), [], [], [], [], [], {})
        for args, expected in [([], True), (['--full-video'], False)]:
            with patch('goprovbox.__main__.scan_folder', return_value=scan), patch('goprovbox.__main__.export') as dispatch:
                self.assertEqual(main(['recordings'] + args), 0)
            self.assertEqual(dispatch.call_args.kwargs['overlap_only'], expected)

    def test_crop_option_reaches_export_for_each_video(self):
        clip = video(Path('clip.mp4'))
        scan = Scan(Path('.'), [clip], [], [], [], [], {})
        with patch('goprovbox.__main__.scan_folder', return_value=scan), patch('goprovbox.__main__.export') as dispatch:
            self.assertEqual(main(['recordings', '--crop', 'bottom']), 0)
        self.assertEqual(dispatch.call_args.kwargs['crops'], {'clip.mp4': 'bottom'})

    def test_original_video_option_is_removed_before_scan(self):
        with patch("goprovbox.__main__.scan_folder") as scan, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(["unused-folder", "--video-mode", "original"])
            self.assertEqual(error.exception.code, 2); scan.assert_not_called()
    def test_report_cannot_overwrite_a_file_skipped_by_scan(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            original = folder / "original-vbox-video.mp4"
            original.write_bytes(b"original recording")
            scan = Scan(folder, [], [], [], [], [], {})
            with patch("goprovbox.__main__.scan_folder", return_value=scan), redirect_stderr(StringIO()):
                self.assertEqual(main([str(folder), "--scan", "--report", str(original)]), 2)
            self.assertEqual(original.read_bytes(), b"original recording")

    def test_report_protects_original_in_separate_gopro_folder(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            runs = folder / 'runs'; runs.mkdir()
            card = folder / 'card'; card.mkdir()
            original = card / 'GH010001.MP4'
            original.write_bytes(b'camera recording')
            scan = Scan(runs, [], [], [], [], [], {original.name: {}},
                        gopro_folder=card, source_paths={original.name: str(original)})
            error = StringIO()
            with patch('goprovbox.__main__.scan_folder', return_value=scan), redirect_stderr(error):
                self.assertEqual(main([str(runs), '--gopro-folder', str(card), '--scan', '--report', str(original)]), 2)
            self.assertIn('must not overwrite an original recording', error.getvalue())
            self.assertEqual(original.read_bytes(), b'camera recording')

    def test_new_scan_report_is_readable_even_when_no_matches(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            report = folder / "scan.json"
            scan = Scan(folder, [], [], [], [], [], {})
            with patch("goprovbox.__main__.scan_folder", return_value=scan):
                self.assertEqual(main([str(folder), "--scan", "--report", str(report)]), 2)
            self.assertEqual(json.loads(report.read_text())["matches"], [])

    def test_interrupted_scan_has_cancellation_exit_code(self):
        with patch("goprovbox.__main__.scan_folder", side_effect=InterruptedError), redirect_stderr(StringIO()):
            self.assertEqual(main(["unused-folder", "--scan"]), 130)


if __name__ == "__main__":
    unittest.main()
