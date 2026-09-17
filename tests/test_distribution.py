from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import hashlib
import io
import os
import shutil
import unittest
import zipfile

from goprovbox import distribution as d
from goprovbox.engine import scan_folder
from goprovbox.media import executable


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        env = patch.dict(os.environ, {"GOPROVBOX_DATA_DIR": str(self.folder / "data")})
        env.start(); self.addCleanup(env.stop)

    def archive(self):
        file = self.folder / "tools.zip"
        with zipfile.ZipFile(file, "w") as z:
            for name in ("bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE", "README.txt"):
                z.writestr(f"ffmpeg-{d.FFMPEG_VERSION}-essentials_build/" + name, b"test payload")
            z.writestr("../escape.exe", b"must never extract")
        return file

    def test_tampered_download_never_creates_installation(self):
        with self.assertRaisesRegex(ValueError, "integrity"):
            d.install_archive(self.archive(), self.folder / "installed")
        self.assertFalse((self.folder / "installed").exists())

    def test_verified_archive_extracts_only_allowlisted_files_and_never_overwrites(self):
        file = self.archive(); dest = self.folder / "installed"
        with patch.object(d, "FFMPEG_SHA256", hashlib.sha256(file.read_bytes()).hexdigest()):
            d.install_archive(file, dest)
            self.assertEqual({p.name for p in dest.iterdir()}, {"ffmpeg.exe", "ffprobe.exe", "LICENSE.txt", "README.txt"})
            self.assertFalse((self.folder / "escape.exe").exists())
            with self.assertRaises(OSError): d.install_archive(file, dest)
        self.assertEqual((dest / "ffmpeg.exe").read_bytes(), b"test payload")

    def test_cancel_does_not_publish(self):
        cancel = Event(); cancel.set()
        with self.assertRaises(InterruptedError):
            d.install_archive(self.archive(), self.folder / "installed", cancel)
        self.assertFalse((self.folder / "installed").exists())

    def test_truncated_network_response_does_not_publish(self):
        response = io.BytesIO(b"truncated"); response.url = "https://example.com/asset"
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "incomplete"): d.download_tools()
        self.assertFalse(d.tools_directory().exists())

    def test_demo_copies_are_independent_and_writable(self):
        first, second = d.copy_demo(), d.copy_demo()
        self.assertNotEqual(first, second)
        name = "GH010001.MP4"
        original = d.asset("demo") / name
        expected = original.read_bytes()
        (first / name).write_bytes(b"modified practice copy")
        self.assertEqual((second / name).read_bytes(), expected)
        self.assertEqual(original.read_bytes(), expected)

    def test_managed_tools_are_found_without_path(self):
        d.tools_directory().mkdir(parents=True)
        for name in ("ffmpeg", "ffprobe"):
            (d.tools_directory() / (name + ".exe")).write_bytes(b"fixture")
        with patch("shutil.which", return_value=None), patch.dict(os.environ, {"GOPROVBOX_FFMPEG":"", "GOPROVBOX_FFPROBE":""}):
            self.assertTrue(d.tools_ready())
            self.assertEqual(Path(executable("ffmpeg")).parent, d.tools_directory())

    def test_missing_tools_returns_false(self):
        with patch("goprovbox.media.executable", side_effect=ValueError("missing")):
            self.assertFalse(d.tools_ready())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_packaged_demo_uses_real_gps_timing_and_partial_overlap(self):
        scan = scan_folder(d.asset("demo"))
        self.assertFalse(scan.errors)
        self.assertEqual(len(scan.videos), 2)
        self.assertEqual([len(m.rows) for m in scan.matches], [100, 101])
        self.assertEqual([v.orientation.clockwise for v in scan.videos], [0, 0])
        self.assertEqual([v.clock.anchors for v in scan.videos], [12, 12])
        self.assertTrue(all(m.position_median_m < .01 for m in scan.matches))


if __name__ == "__main__":
    unittest.main()
