from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json

from goprovbox.engine import Scan, fingerprint, tidy_export, write_report
from goprovbox.gpmf import TelemetryError
from goprovbox.reports import archive_details, export_report_path, records_directory
from tests.export_case import ExportTestCase


class ExportReportsTests(ExportTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve() / 'Results & laps #1'
        self.folder.mkdir()
        for name, content in {'clip.mp4': b'video', 'clip.vbo': b'telemetry',
                              'clip.jpg': b'preview', 'clip.encoding.log': b'encoding details',
                              'clip.audio.audio.log': b'audio details', 'clip.audio-mux.log': b'mux details',
                              'OPEN IN CIRCUIT TOOLS.txt': b'old instructions'}.items():
            (self.folder / name).write_bytes(content)
        self.report = Scan(self.folder.parent, [], [], [], [], [], {}).summary() | {
            'status': 'complete', 'settings': {}, 'output_folder': str(self.folder),
            'outputs': [{'file': 'clip.vbo', 'overlap_seconds': 2, 'samples': 20,
                         'utc_start': '2026-09-29T10:00:00Z', 'utc_end': '2026-09-29T10:00:02Z'}],
            'media': [{'file': 'clip.mp4', 'source': 'gopro.mp4', 'preview': 'clip.jpg',
                       'rotation_clockwise': 0, 'verification': {'dimensions': [1920, 1080], 'codec': 'h264'}}],
            'output_fingerprints': {name: fingerprint(self.folder / name) for name in ('clip.mp4', 'clip.vbo')}}
        (self.folder / 'report.json').write_text(json.dumps(self.report), encoding='utf-8')
        write_report(self.report, self.folder / 'Report.html')

    def test_tidy_keeps_media_identical_and_report_links_work(self):
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        record = tidy_export(self.folder)
        self.assertEqual({p.name for p in self.folder.iterdir()}, {'clip.mp4', 'clip.vbo'})
        for name in ('clip.mp4', 'clip.vbo'):
            self.assertEqual((self.folder / name).read_bytes(), before[name])
        for name in ('clip.jpg', 'clip.encoding.log', 'clip.audio.audio.log', 'clip.audio-mux.log',
                     'OPEN IN CIRCUIT TOOLS.txt'):
            self.assertEqual((record / name).read_bytes(), before[name])
        self.assertEqual((record / 'previous-report.json').read_bytes(), before['report.json'])
        self.assertEqual((record / 'previous-Report.html').read_bytes(), before['Report.html'])
        self.assertEqual(export_report_path(self.folder), record / 'Report.html')
        html = export_report_path(self.folder).read_text(encoding='utf-8')
        self.assertIn((self.folder / 'clip.vbo').as_uri().replace('&', '&amp;'), html)
        self.assertIn('src="clip.jpg"', html)
        self.assertEqual(tidy_export(self.folder), record)

    def test_tidy_leaves_unrecognised_user_files_alone(self):
        (self.folder / 'my notes.txt').write_text('keep me')
        tidy_export(self.folder)
        self.assertEqual((self.folder / 'my notes.txt').read_text(), 'keep me')

    def test_changed_media_prevents_tidy(self):
        (self.folder / 'clip.mp4').write_bytes(b'edited video')
        with self.assertRaisesRegex(TelemetryError, 'has changed'):
            tidy_export(self.folder)
        self.assertTrue((self.folder / 'report.json').is_file())
        self.assertTrue((self.folder / 'clip.jpg').is_file())

    def test_report_write_failure_preserves_all_original_files(self):
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        with patch('goprovbox.engine.write_report', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                tidy_export(self.folder)
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir()}, before)
        self.assertEqual(export_report_path(self.folder), self.folder / 'Report.html')
        archived = list(records_directory(self.folder).glob('*/report.json'))
        self.assertEqual(json.loads(archived[0].read_text())['status'], 'failed')

    def test_corrupt_and_failed_newer_records_do_not_hide_the_completed_report(self):
        record = tidy_export(self.folder)
        for name, content in [('9999999999999999999-a', '{broken'),
                              ('9999999999999999999-b', '{"status":"failed"}')]:
            other = record.parent / name; other.mkdir()
            (other / 'report.json').write_text(content)
        self.assertEqual(export_report_path(self.folder), record / 'Report.html')

    def test_preview_cannot_name_a_media_file_or_escape_the_folder(self):
        for preview in ('clip.mp4', '../elsewhere.jpg', '..\\elsewhere.jpg'):
            self.report['media'][0]['preview'] = preview
            with self.assertRaises(ValueError):
                archive_details(self.folder, self.folder, self.report, write_report)
            self.assertEqual((self.folder / 'clip.mp4').read_bytes(), b'video')
