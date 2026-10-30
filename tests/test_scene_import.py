"""Encrypted scenes use the installed public importer; saved copies stay local."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import io
import hashlib
import tarfile
import unittest
import xml.etree.ElementTree as ET

from goprovbox.gpmf import TelemetryError
from goprovbox.overlay import load_scene, read_scene_archive
from tests.test_overlay import scene_members


class SceneImportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.scene = self.folder / 'custom.VVHSN'
        self.scene.write_bytes(b'synthetic encrypted scene placeholder')
        replace_os = patch('goprovbox.overlay.os', SimpleNamespace(name='nt'))
        replace_os.start()
        self.addCleanup(replace_os.stop)

    def test_encrypted_scene_uses_installed_public_importer_and_preserves_input(self):
        original = self.scene.read_bytes()
        with patch('goprovbox.scene_reader.read_scene_files', return_value=scene_members()) as importer:
            loaded = load_scene(self.scene)
        self.assertTrue(loaded.elements)
        self.assertEqual(loaded.digest, hashlib.sha256(original).hexdigest())
        self.assertEqual(importer.call_args.args[0], self.scene)
        self.assertEqual(self.scene.read_bytes(), original)

    def test_importer_failure_is_not_replaced_with_the_old_decrypt_error(self):
        with patch('goprovbox.scene_reader.read_scene_files', side_effect=TelemetryError('VBOX scene import failed: invalid scene')):
            with self.assertRaisesRegex(TelemetryError, 'VBOX scene import failed: invalid scene'):
                read_scene_archive(self.scene)

    def test_encrypted_scene_changed_during_import_is_rejected(self):
        def changed(path, *args, **kwargs):
            path.write_bytes(b'changed during vendor import')
            return scene_members()
        with patch('goprovbox.scene_reader.read_scene_files', side_effect=changed):
            with self.assertRaisesRegex(TelemetryError, 'Scene changed while being read'):
                load_scene(self.scene)

    def package(self, members=None, mode='w'):
        path = self.folder / 'practice.gvscene'
        with tarfile.open(path, mode) as archive:
            for name, data in (scene_members() if members is None else members).items():
                member = tarfile.TarInfo(name); member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_saved_package_loads_full_and_four_channel_scenes_without_vendor_helper(self):
        members = scene_members()
        root = ET.fromstring(members['SCENE.XML'])
        laps = root.find(".//graphics/text[@name='laps']")
        laps.set('font', 'digits'); laps.set('offset', '(0,0,70,12)')
        laps.set('fmtfn', 'valid_time_ms'); laps.set('fmtstr', '%02u:%05.2f')
        track = ET.SubElement(root.find('.//graphics'), 'map', name='track', reserved_size='(100,80)')
        ET.SubElement(track, 'dynamic_point', image='bar')
        ET.SubElement(root.find('.//layout'), 'graphic', name='track', screen_pos='(10,10)')
        members['SCENE.XML'] = ET.tostring(root)
        path = self.package(members)
        original = path.read_bytes()
        # Saved packages also work without Windows or an installed scene editor.
        with patch('goprovbox.overlay.os', SimpleNamespace(name='posix')), \
                patch('goprovbox.scene_reader.read_scene_files') as helper:
            for mode in ('four', 'full'):
                with self.subTest(mode=mode):
                    scene = load_scene(path, mode)
                    self.assertEqual(scene.mode, mode)
                    self.assertEqual(scene.name, path.name)
                    self.assertEqual(scene.digest, hashlib.sha256(original).hexdigest())
                    self.assertIn('Loaded from a local saved scene package.', scene.summary()['notes'])
                    self.assertTrue(scene.fonts); self.assertTrue(scene.assets)
                    if mode == 'full':
                        self.assertTrue(any(item['kind'] == 'map' for item in scene.elements))
            helper.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_saved_package_rejects_invalid_missing_and_unsafe_contents(self):
        invalid = self.folder / 'invalid.gvscene'
        invalid.write_bytes(b'not a TAR archive')
        with self.assertRaisesRegex(TelemetryError, 'Invalid scene archive'):
            load_scene(invalid)
        missing = self.package({'SCENE.XML': b'<RLCFG />'})
        with self.assertRaisesRegex(TelemetryError, 'Saved scene package is missing VBOXHD.XML'):
            load_scene(missing)
        unsafe = self.package({'../SCENE.XML': b'<RLCFG />'})
        with self.assertRaisesRegex(TelemetryError, 'Unsafe member'):
            load_scene(unsafe)
        malformed = self.package({'SCENE.XML': b'<broken', 'VBOXHD.XML': b'<RLCFG />'})
        with self.assertRaisesRegex(TelemetryError, 'Invalid scene XML'):
            load_scene(malformed)

    def test_saved_package_keeps_raw_and_expanded_size_limits(self):
        path = self.package()
        with patch('goprovbox.overlay.MAX_ARCHIVE', 8):
            with self.assertRaisesRegex(TelemetryError, 'Scene file exceeds'):
                load_scene(path)
        compressed = self.package({'large.bin': b'x' * 4097}, mode='w:gz')
        self.assertLess(compressed.stat().st_size, 4096)
        with patch('goprovbox.overlay.MAX_ARCHIVE', 4096):
            with self.assertRaisesRegex(TelemetryError, 'Scene archive exceeds size limits'):
                load_scene(compressed)

    def test_saved_package_changed_during_import_is_rejected(self):
        path = self.package()
        original_reader = read_scene_archive
        def changing_reader(source):
            members = original_reader(source)
            source.write_bytes(b'changed after reading')
            return members
        with patch('goprovbox.overlay.read_scene_archive', side_effect=changing_reader):
            with self.assertRaisesRegex(TelemetryError, 'Scene changed while being read'):
                load_scene(path)


if __name__ == '__main__':
    unittest.main()
