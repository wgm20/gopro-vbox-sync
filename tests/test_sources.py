"""Separate VBOX folders and recursive, removable GoPro sources."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import shutil
import unittest

from goprovbox.engine import export, scan_folder, select_for_export
from goprovbox.gpmf import TelemetryError
from goprovbox.reports import export_report_path
from tests.export_case import ExportTestCase
from tests.test_core import fixture, video


class SourceTests(ExportTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.vbox = self.root / "VBOX runs"
        self.card = self.root / "SD card"
        self.vbox.mkdir(); self.card.mkdir()
        fixture(self.vbox / "VBOX0001.vbo")

    def clip(self, relative, root=None):
        path = (root or self.card) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic GoPro recording")
        return path

    def scan(self, **kwargs):
        with patch("goprovbox.engine.executable", return_value="tool"), \
                patch("goprovbox.engine.probe", return_value={"streams": [{"codec_tag_string": "gpmd"}]}), \
                patch("goprovbox.engine.inspect_video", side_effect=lambda p, *_: video(p, duration=3)):
            return scan_folder(self.vbox, **kwargs)

    def test_separate_card_scans_nested_videos_and_top_level_vbox_only(self):
        chosen = self.clip("DCIM/100GOPRO/GH010001.MP4")
        self.clip("unselected.mp4", self.vbox)
        (self.card / "other.vbo").write_bytes(b"not VBOX data")
        fixture(self.vbox / "nested.vbo")
        nested = self.vbox / "subfolder"
        nested.mkdir()
        fixture(nested / "VBOX9999.vbo")
        scan = self.scan(gopro_folder=self.card, recursive_gopro=True)
        self.assertEqual([v.path for v in scan.videos], [chosen])
        self.assertEqual({v.path.name for v in scan.vbos}, {"VBOX0001.vbo", "nested.vbo"})
        self.assertTrue(scan.matches)
        self.assertEqual(scan.folder, self.vbox.resolve())
        self.assertEqual(scan.gopro_folder, self.card.resolve())
        self.assertEqual(scan.source_path(chosen.name), chosen)
        self.assertEqual(scan.source_path("VBOX0001.vbo"), self.vbox / "VBOX0001.vbo")
        self.assertEqual(scan.summary()["gopro_folder"], str(self.card))
        self.assertEqual(scan.summary()["source_paths"][chosen.name], str(chosen))

    def test_legacy_scan_stays_top_level_and_recursion_can_be_enabled_in_place(self):
        direct = self.clip("GH010001.mp4", self.vbox)
        nested = self.clip("DCIM/GH010002.mp4", self.vbox)
        self.assertEqual([v.path for v in self.scan().videos], [direct])
        self.assertEqual({v.path for v in self.scan(recursive_gopro=True).videos}, {direct, nested})

    def test_card_recursion_can_be_disabled(self):
        direct = self.clip("GH010001.mp4")
        self.clip("DCIM/GH010002.mp4")
        self.assertEqual([v.path for v in self.scan(gopro_folder=self.card).videos], [direct])

    def test_duplicate_valid_gopro_names_stop_scan_with_both_locations(self):
        first = self.clip("100GOPRO/GH010001.MP4")
        second = self.clip("101GOPRO/gh010001.mp4")
        with self.assertRaises(TelemetryError) as caught:
            self.scan(gopro_folder=self.card, recursive_gopro=True)
        self.assertIn("duplicate filenames", str(caught.exception))
        self.assertIn(str(first), str(caught.exception))
        self.assertIn(str(second), str(caught.exception))
        self.assertIn("narrower GoPro folder", str(caught.exception))

    def test_exports_original_vbox_and_files_without_gopro_telemetry_are_skipped(self):
        wanted = self.clip("DCIM/GH010001.mp4")
        not_gopro = self.clip("other/GH010001.mp4")
        for relative in ("GoPro Circuit Tools Matched/GH010002.mp4", ".gvs-deadbeef/GH010003.mp4",
                         "DCIM/GoPro_GH010004_0001.mp4", "DCIM/VBOX0001_0001.mp4"):
            self.clip(relative)
        seen = []
        def metadata(path):
            seen.append(path)
            return {"streams": [] if path == not_gopro else [{"codec_tag_string": "gpmd"}]}
        with patch("goprovbox.engine.executable", return_value="tool"), \
                patch("goprovbox.engine.probe", side_effect=metadata), \
                patch("goprovbox.engine.inspect_video", side_effect=lambda p, *_: video(p, duration=3)):
            scan = scan_folder(self.vbox, gopro_folder=self.card, recursive_gopro=True)
        self.assertEqual([v.path for v in scan.videos], [wanted])
        self.assertEqual(set(seen), {wanted, not_gopro})

    def test_recursive_scan_does_not_follow_junctions(self):
        wanted = self.clip("DCIM/GH010001.mp4")
        self.clip("linked/GH010002.mp4")
        original_lstat = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == self.card / "linked":
                return SimpleNamespace(st_reparse_tag=0xA0000003)
            return original_lstat(path, *args, **kwargs)
        with patch.object(Path, "lstat", lstat):
            scan = self.scan(gopro_folder=self.card, recursive_gopro=True)
        self.assertEqual([v.path for v in scan.videos], [wanted])

    def test_protected_sd_subfolder_is_reported_without_losing_accessible_videos(self):
        wanted = self.clip("DCIM/GH010001.mp4")
        protected = self.card / "System Volume Information"
        protected.mkdir()
        original_iterdir = Path.iterdir
        def iterdir(path):
            if path == protected:
                raise PermissionError("Access denied")
            return original_iterdir(path)
        with patch.object(Path, "iterdir", iterdir):
            scan = self.scan(gopro_folder=self.card, recursive_gopro=True)
        self.assertEqual([v.path for v in scan.videos], [wanted])
        self.assertTrue(scan.matches)
        self.assertEqual(len(scan.errors), 1)
        self.assertIn("System Volume Information", scan.errors[0])

    def test_cancelled_discovery_never_probes_videos(self):
        for number in range(20):
            self.clip(f"DCIM/GH01{number:04d}.mp4")
        cancel = Mock()
        cancel.is_set.side_effect = [False] * 6 + [True]
        with patch("goprovbox.engine.executable", return_value="tool"), \
                patch("goprovbox.engine.probe") as probe:
            with self.assertRaises(InterruptedError):
                scan_folder(self.vbox, gopro_folder=self.card, recursive_gopro=True, cancel=cancel)
        probe.assert_not_called()
        cancelled = Event(); cancelled.set()
        with self.assertRaises(InterruptedError):
            scan_folder(self.vbox, gopro_folder=self.card, cancel=cancelled)

    def test_selection_keeps_paths_for_only_selected_video_and_linked_data(self):
        first = self.clip("100GOPRO/GH010001.mp4")
        second = self.clip("101GOPRO/GH010002.mp4")
        scan = self.scan(gopro_folder=self.card, recursive_gopro=True)
        selected = select_for_export(scan, {second.name})
        self.assertEqual(set(selected.source_paths), {second.name, "VBOX0001.vbo"})
        self.assertNotIn(first.name, selected.fingerprints)
        self.assertEqual(selected.source_path(second.name), second)
        self.assertEqual(len(scan.source_paths), 3)

    def test_changed_or_removed_card_video_is_checked_at_its_real_location(self):
        path = self.clip("DCIM/GH010001.mp4")
        scan = self.scan(gopro_folder=self.card, recursive_gopro=True)
        self.clip(path.name, self.vbox)  # A same-named local file is not the source.
        path.write_bytes(b"changed")
        with patch("goprovbox.engine.encode") as encode:
            with self.assertRaisesRegex(TelemetryError, "changed since scan"):
                export(scan, audio_source="gopro")
            path.unlink()
            with self.assertRaisesRegex(TelemetryError, "Reconnect its drive or SD card"):
                export(scan, audio_source="gopro")
        encode.assert_not_called()

    def test_output_cannot_be_source_root_or_contain_source_video(self):
        self.clip("DCIM/100GOPRO/GH010001.mp4")
        scan = self.scan(gopro_folder=self.card, recursive_gopro=True)
        for output in (self.vbox, self.card, self.root, self.card / "DCIM"):
            with self.subTest(output=output), self.assertRaisesRegex(TelemetryError, "separate output folder"):
                export(scan, output, audio_source="gopro")

    def test_missing_source_root_names_which_folder_is_unavailable(self):
        with self.assertRaisesRegex(TelemetryError, "GoPro folder does not exist"):
            self.scan(gopro_folder=self.root / "removed SD card")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_scan_and_export_from_card_keeps_output_beside_vbox(self):
        demo = Path(__file__).resolve().parents[1] / "goprovbox" / "assets" / "demo"
        shutil.copyfile(demo / "VBOX0001.vbo", self.vbox / "VBOX0001.vbo")
        dcim = self.card / "DCIM" / "100GOPRO"
        dcim.mkdir(parents=True)
        for path in demo.glob("*.MP4"):
            shutil.copyfile(path, dcim / path.name)
        scan = scan_folder(self.vbox, gopro_folder=self.card, recursive_gopro=True)
        self.assertEqual(len(scan.videos), 2)
        chosen = {scan.videos[0].path.name}
        result = export(scan, include_videos=chosen, audio_source="gopro", encoder="software", max_size=128)
        self.assertEqual(result.parent, self.vbox)
        self.assertEqual(len(list(result.glob("*.mp4"))), 1)
        self.assertEqual(list(self.vbox.glob("*.mp4")), [])
        report = json.loads(export_report_path(result, "report.json").read_text())
        self.assertTrue(report["outputs"][0]["telemetry_preserved"])
        self.assertEqual(report["gopro_folder"], str(self.card))
        self.assertEqual(Path(report["source_paths"][next(iter(chosen))]).parent, dcim)
        self.assertEqual(export(scan, result, include_videos=chosen, audio_source="gopro",
                                encoder="software", max_size=128), result)


if __name__ == "__main__":
    unittest.main()
