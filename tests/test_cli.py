from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from goprovbox.__main__ import main
from goprovbox.engine import Scan


class CLITests(unittest.TestCase):
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
