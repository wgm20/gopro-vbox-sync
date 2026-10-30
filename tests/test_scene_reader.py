"""The public scene importer is isolated and its returned files are bounded."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import stat
import subprocess
import unittest

from goprovbox import scene_reader as reader
from goprovbox.gpmf import TelemetryError


def write_members(folder, members=None):
    members = members or {'SCENE.XML': b'<scene/>', 'VBOXHD.XML': b'<config/>'}
    for name, contents in members.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return members


class SceneDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / 'imported scene'
        self.folder.mkdir()

    def test_unchanged_helper_build_reuses_existing_binary(self):
        windows = self.folder / 'Windows'
        compiler = windows / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
        compiler.parent.mkdir(parents=True); compiler.touch()
        destination = self.folder / 'helper'
        def compile_host(args, **kwargs):
            output = next(arg[5:] for arg in args if arg.startswith('/out:'))
            Path(output).write_bytes(b'synthetic managed helper')
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        with patch.dict(os.environ, {'WINDIR': str(windows)}), \
                patch.object(reader.subprocess, 'run', side_effect=compile_host) as compile:
            first = reader.build_helper(destination)
            self.assertEqual(reader.build_helper(destination), first)
            compile.assert_called_once()
            # A corrupt binary must never count as an up-to-date helper.
            first.write_bytes(b'corrupt')
            self.assertEqual(reader.build_helper(destination), first)
            self.assertEqual(compile.call_count, 2)
        self.assertEqual(first.read_bytes(), b'synthetic managed helper')

    def test_nested_artwork_is_read_without_flattening_names(self):
        members = {'SCENE.XML': b'<scene/>', 'VBOXHD.XML': b'<config/>',
                   'images/dial.png': b'dial fixture', 'fonts/digits.png': b'font fixture'}
        write_members(self.folder, members)
        self.assertEqual(reader.directory_members(self.folder), members)

    def test_both_scene_configuration_files_are_required(self):
        for missing in ('SCENE.XML', 'VBOXHD.XML'):
            with self.subTest(missing=missing):
                folder = self.folder / missing.replace('.', '-')
                folder.mkdir()
                remaining = {'SCENE.XML': b'<scene/>', 'VBOXHD.XML': b'<config/>'}
                del remaining[missing]
                write_members(folder, remaining)
                with self.assertRaisesRegex(TelemetryError, 'missing ' + missing):
                    reader.directory_members(folder)

    def test_total_bytes_and_file_count_are_limited(self):
        write_members(self.folder, {'SCENE.XML': b'<scene/>', 'VBOXHD.XML': b'<config/>',
                                    'images/extra.png': b'x' * 20})
        for options in ({'max_size': 25}, {'max_entries': 2}):
            with self.subTest(options=options), self.assertRaisesRegex(TelemetryError, 'limit'):
                reader.directory_members(self.folder, **options)

    def test_multiple_dial_needle_sets_fit_within_the_default_file_limit(self):
        write_members(self.folder)
        needles = self.folder / 'images' / 'needles'
        needles.mkdir(parents=True)
        for number in range(720):
            (needles / f'{number}.png').write_bytes(b'synthetic needle frame')
        self.assertEqual(len(reader.directory_members(self.folder)), 722)

    def test_linked_members_and_root_are_rejected(self):
        write_members(self.folder)
        linked = self.folder / 'linked'
        linked.mkdir()
        original_lstat = Path.lstat

        class StatWithLink:
            def __init__(self, original, mode, tag):
                self.original = original
                self.st_mode = mode
                self.st_reparse_tag = tag

            def __getattr__(self, name):
                return getattr(self.original, name)

        for target, mode, tag in ((linked, stat.S_IFLNK, 0),
                                  (linked, stat.S_IFDIR, 0xA0000003),
                                  (self.folder, stat.S_IFLNK, 0),
                                  (self.folder, stat.S_IFDIR, 0xA0000003)):
            def lstat(path, *args, **kwargs):
                original = original_lstat(path, *args, **kwargs)
                return StatWithLink(original, mode, tag) if path == target else original
            with self.subTest(target=target, mode=mode, tag=tag), patch.object(Path, 'lstat', lstat):
                with self.assertRaisesRegex(TelemetryError, 'unsafe|linked'):
                    reader.directory_members(self.folder)

    def test_hardlinked_members_and_unsupported_files_are_rejected(self):
        write_members(self.folder)
        target = self.folder / 'SCENE.XML'
        original_lstat = Path.lstat

        class ChangedStat:
            def __init__(self, original, changes):
                self.original = original
                self.__dict__.update(changes)

            def __getattr__(self, name):
                return getattr(self.original, name)

        for changes in ({'st_nlink': 2}, {'st_mode': stat.S_IFIFO}):
            def lstat(path, *args, **kwargs):
                original = original_lstat(path, *args, **kwargs)
                return ChangedStat(original, changes) if path == target else original
            with self.subTest(changes=changes), patch.object(Path, 'lstat', lstat):
                with self.assertRaisesRegex(TelemetryError, 'linked|unsupported'):
                    reader.directory_members(self.folder)

    def test_file_changed_while_being_read_is_rejected(self):
        write_members(self.folder)
        original_read = Path.read_bytes
        def changed(path):
            result = original_read(path)
            return result + b'changed' if path.name == 'SCENE.XML' else result
        with patch.object(Path, 'read_bytes', changed):
            with self.assertRaisesRegex(TelemetryError, 'changed while being read'):
                reader.directory_members(self.folder)


class PublicSceneReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.scene = self.folder / "my scene &'quoted.VVHSN"
        self.scene.write_bytes(b'synthetic encrypted scene')
        self.dotnet = self.folder / 'dotnet.exe'
        self.helper = self.folder / 'scene-reader.dll'
        self.library = self.folder / 'Installed VBOX Video'
        for name, value in (('find_dotnet', self.dotnet), ('helper_path', self.helper),
                            ('library_directory', self.library)):
            replace = patch.object(reader, name, return_value=value)
            replace.start()
            self.addCleanup(replace.stop)

    def successful_helper(self, command, **kwargs):
        destination = Path(command[-1]) / 'scene'
        write_members(destination, {'SCENE.XML': b'<scene/>', 'VBOXHD.XML': b'<config/>',
                                    'images/dial.png': b'artwork'})
        return subprocess.CompletedProcess(command, 0, stdout=str(destination) + '\n', stderr='')

    def test_arguments_are_literal_paths_and_imported_assets_are_returned(self):
        original = self.scene.read_bytes()
        with patch.object(reader.subprocess, 'run', side_effect=self.successful_helper) as helper:
            members = reader.read_scene_files(self.scene)
        command = helper.call_args.args[0]
        self.assertEqual(command[:4], [str(self.dotnet), str(self.helper), str(self.library), str(self.scene.resolve())])
        self.assertEqual(len(command), 5)
        self.assertEqual(members['images/dial.png'], b'artwork')
        self.assertFalse(Path(command[-1]).exists(), 'temporary importer output should be cleaned up')
        self.assertEqual(self.scene.read_bytes(), original)
        self.assertNotIn('shell', helper.call_args.kwargs)

    def test_helper_errors_and_missing_desktop_runtime_are_actionable(self):
        for stderr, expected in [('Scene reader: Unsupported scene version', 'VBOX scene import failed'),
                                 ('You must install or update .NET. Microsoft.WindowsDesktop.App', 'Desktop Runtime')]:
            result = subprocess.CompletedProcess([], 1, stdout='', stderr=stderr)
            with self.subTest(stderr=stderr), patch.object(reader.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(TelemetryError, expected):
                    reader.read_scene_files(self.scene)

    def test_helper_timeout_is_reported_as_a_scene_timeout(self):
        with patch.object(reader.subprocess, 'run', side_effect=subprocess.TimeoutExpired('scene-reader', 60)):
            with self.assertRaisesRegex(TelemetryError, 'timed out'):
                reader.read_scene_files(self.scene)

    def test_helper_cannot_return_an_outside_relative_or_invalid_folder(self):
        for stdout, expected in [(str(self.folder), 'outside'), ('relative/path', 'outside'),
                                 ('', 'valid output folder'), ('first\nsecond', 'valid output folder')]:
            result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr='')
            with self.subTest(stdout=stdout), patch.object(reader.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(TelemetryError, expected):
                    reader.read_scene_files(self.scene)

    def test_returned_missing_folder_and_missing_xml_are_rejected(self):
        def missing(command, **kwargs):
            destination = Path(command[-1]) / 'missing'
            return subprocess.CompletedProcess(command, 0, stdout=str(destination), stderr='')
        with patch.object(reader.subprocess, 'run', side_effect=missing):
            with self.assertRaisesRegex(TelemetryError, 'did not create'):
                reader.read_scene_files(self.scene)

        def no_config(command, **kwargs):
            destination = Path(command[-1]) / 'empty'
            destination.mkdir()
            return subprocess.CompletedProcess(command, 0, stdout=str(destination), stderr='')
        with patch.object(reader.subprocess, 'run', side_effect=no_config):
            with self.assertRaisesRegex(TelemetryError, 'missing .*SCENE.XML'):
                reader.read_scene_files(self.scene)

    def test_source_size_stops_helper_and_expanded_limits_reach_file_reader(self):
        with patch.object(reader.subprocess, 'run') as helper:
            with self.assertRaisesRegex(TelemetryError, 'size limit'):
                reader.read_scene_files(self.scene, max_size=4)
            helper.assert_not_called()
        with patch.object(reader.subprocess, 'run', side_effect=self.successful_helper):
            with self.assertRaisesRegex(TelemetryError, 'limit'):
                reader.read_scene_files(self.scene, max_entries=2)


class SceneReaderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        replace = patch.dict(os.environ, {'ProgramFiles': str(self.folder / 'Programs'),
                                          'ProgramFiles(x86)': str(self.folder / 'Programs x86'),
                                          'GOPROVBOX_VBOX_VIDEO': '', 'GOPROVBOX_DOTNET': '',
                                          'DOTNET_ROOT_X64': '', 'DOTNET_ROOT': ''})
        replace.start()
        self.addCleanup(replace.stop)

    def installed_library(self, folder):
        folder.mkdir(parents=True)
        (folder / 'VBoxVideo.FileRoutines.dll').touch()
        return folder.resolve()

    def test_vendor_install_can_be_found_in_either_program_files_location(self):
        for variable in ('ProgramFiles(x86)', 'ProgramFiles'):
            folder = Path(os.environ[variable]) / 'Racelogic' / 'VBOX Video'
            expected = self.installed_library(folder)
            with self.subTest(variable=variable):
                self.assertEqual(reader.library_directory(), expected)
            (folder / 'VBoxVideo.FileRoutines.dll').unlink()

    def test_explicit_vendor_install_is_preferred_and_missing_install_is_explained(self):
        preferred = self.installed_library(self.folder / 'Custom VBOX Video')
        self.installed_library(Path(os.environ['ProgramFiles']) / 'Racelogic' / 'VBOX Video')
        with patch.dict(os.environ, {'GOPROVBOX_VBOX_VIDEO': str(preferred)}):
            self.assertEqual(reader.library_directory(), preferred)
        with patch.dict(os.environ, {'ProgramFiles': str(self.folder / 'missing'),
                                     'ProgramFiles(x86)': str(self.folder / 'also missing')}):
            with self.assertRaisesRegex(TelemetryError, 'Install VBOX Video Setup'):
                reader.library_directory()

    def test_dotnet_override_standard_install_and_missing_runtime(self):
        explicit = self.folder / 'Custom runtime' / 'dotnet.exe'
        explicit.parent.mkdir(); explicit.touch()
        standard = Path(os.environ['ProgramFiles']) / 'dotnet' / 'dotnet.exe'
        standard.parent.mkdir(parents=True); standard.touch()
        with patch.object(reader.shutil, 'which', return_value=None):
            with patch.dict(os.environ, {'GOPROVBOX_DOTNET': str(explicit)}):
                self.assertEqual(reader.find_dotnet(), explicit.resolve())
            self.assertEqual(reader.find_dotnet(), standard.resolve())
            standard.unlink()
            with self.assertRaisesRegex(TelemetryError, 'Desktop Runtime'):
                reader.find_dotnet()

    def test_dotnet_root_and_path_installations_are_supported(self):
        runtime = self.folder / 'Runtime root'; runtime.mkdir()
        host = runtime / 'dotnet.exe'; host.touch()
        with patch.object(reader.shutil, 'which', return_value=None), \
                patch.dict(os.environ, {'DOTNET_ROOT_X64': str(runtime)}):
            self.assertEqual(reader.find_dotnet(), host.resolve())
        with patch.object(reader.shutil, 'which', return_value=str(host)):
            self.assertEqual(reader.find_dotnet(), host.resolve())


if __name__ == '__main__':
    unittest.main()
