"""Header Options actions preserve settings and keep changes locked while busy."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import tkinter as tk
import unittest

from goprovbox.gui import App


class OptionsMenuTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.settings = self.folder / 'settings.json'
        self.settings.write_text(json.dumps({'welcomed': True, 'folder': str(self.folder)}))
        settings = patch('goprovbox.gui.data_directory', return_value=self.folder)
        settings.start(); self.addCleanup(settings.stop)
        self.root = tk.Tk(); self.root.withdraw(); self.addCleanup(self.cleanup_root)
        self.app = App(self.root); self.addCleanup(self.app.temporary.cleanup)

    def cleanup_root(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()

    def test_radio_choices_apply_and_overlay_controls_scene_visibility(self):
        app = self.app
        app.resolution_menu.invoke('Compact · 1280 px')
        self.assertEqual(app.quality.get(), 'Compact · 1280 px')
        app.sound_menu.invoke('GoPro')
        self.assertEqual(app.audio_source.get(), 'GoPro')
        app.overlay_menu.invoke('Full scene · no rear camera')
        self.assertEqual(app.overlay_mode.get(), 'Full scene · no rear camera')
        self.assertEqual(app.scenerow.winfo_manager(), 'pack')
        self.assertIn('Overlay', Path(app.output.get()).name)
        app.overlay_menu.invoke('None')
        self.assertEqual(app.overlay_mode.get(), 'None')
        self.assertEqual(app.scenerow.winfo_manager(), '')
        self.assertNotIn('Overlay', Path(app.output.get()).name)

    def test_trim_updates_default_output_without_losing_existing_matches(self):
        app = self.app
        original_scan = SimpleNamespace(matches=[])
        app.scan = original_scan
        self.assertTrue(app.overlap_only.get())
        self.assertTrue(Path(app.output.get()).name.endswith(' Matched'))
        app.options_menu.invoke('Only export video with VBOX data')
        self.assertFalse(app.overlap_only.get())
        self.assertFalse(Path(app.output.get()).name.endswith(' Matched'))
        self.assertIs(app.scan, original_scan)

    def test_subfolder_change_discards_stale_scan_results(self):
        app = self.app
        app.scan = SimpleNamespace(matches=[])
        app.result = self.folder / 'Old result'
        app.included_videos.add('clip.MP4')
        app.tree.insert('', 'end', iid='0', values=('yes', 'clip.MP4'))
        self.assertTrue(app.recursive_gopro.get())
        app.options_menu.invoke('Include GoPro subfolders')
        self.assertFalse(app.recursive_gopro.get())
        self.assertIsNone(app.scan)
        self.assertIsNone(app.result)
        self.assertFalse(app.included_videos)
        self.assertFalse(app.tree.get_children())
        self.assertTrue(app.create.instate(['disabled']))

    def test_menu_choices_are_restored_when_app_reopens(self):
        app = self.app
        app.resolution_menu.invoke('Compact · 1280 px')
        app.overlay_menu.invoke('Driving data')
        app.sound_menu.invoke('GoPro')
        app.options_menu.invoke('Only export video with VBOX data')
        app.options_menu.invoke('Include GoPro subfolders')
        app.save_preferences()
        saved = json.loads(self.settings.read_text())
        self.assertFalse(saved['include_gopro_subfolders'])
        self.assertNotIn('recursive_gopro', saved)
        reopened = tk.Toplevel(self.root)
        restored = App(reopened); self.addCleanup(restored.temporary.cleanup)
        self.addCleanup(reopened.destroy)
        self.assertEqual(restored.quality.get(), 'Compact · 1280 px')
        self.assertEqual(restored.overlay_mode.get(), 'Driving data')
        self.assertEqual(restored.audio_source.get(), 'GoPro')
        self.assertFalse(restored.overlap_only.get())
        self.assertFalse(restored.recursive_gopro.get())

    def test_busy_locks_changes_but_keeps_details_and_explanation_available(self):
        app = self.app
        app.set_busy(True)
        self.assertTrue(app.options_button.instate(['!disabled']))
        mutating = ('Resolution', 'Overlay', 'Sound', 'Only export video with VBOX data',
                    'Include GoPro subfolders')
        for label in mutating:
            self.assertEqual(app.options_menu.entrycget(label, 'state'), 'disabled')
        original = (app.quality.get(), app.overlay_mode.get(), app.audio_source.get(),
                    app.overlap_only.get(), app.recursive_gopro.get())
        app.resolution_menu.invoke('Compact · 1280 px')
        app.overlay_menu.invoke('Full scene · no rear camera')
        app.sound_menu.invoke('GoPro')
        app.options_menu.invoke('Only export video with VBOX data')
        app.options_menu.invoke('Include GoPro subfolders')
        self.assertEqual((app.quality.get(), app.overlay_mode.get(), app.audio_source.get(),
                          app.overlap_only.get(), app.recursive_gopro.get()), original)
        for label in ('Details…', 'What do red stars mean?…'):
            self.assertEqual(app.options_menu.entrycget(label, 'state'), 'normal')
        app.options_menu.invoke('Details…')
        details = [window for window in self.root.winfo_children()
                   if isinstance(window, tk.Toplevel) and window.title() == 'Scan and export details']
        self.assertEqual(len(details), 1)
        with patch('goprovbox.gui.messagebox.showinfo') as explain:
            app.options_menu.invoke('What do red stars mean?…')
        self.assertEqual(explain.call_args.args[0], 'Red stars and lap times')
        self.assertIn('Best in file', explain.call_args.args[1])
        self.assertIn('VBOX', explain.call_args.args[1])
        app.set_busy(False)
        for label in mutating:
            self.assertEqual(app.options_menu.entrycget(label, 'state'), 'normal')


if __name__ == '__main__':
    unittest.main()
