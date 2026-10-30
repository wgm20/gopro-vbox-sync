"""Folder selection keeps card input separate from VBOX data and output."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import tkinter as tk
import unittest

from goprovbox.gui import App


class SourcePickerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.vbox = self.home / "VBOX runs"; self.vbox.mkdir()
        self.card = self.home / "SD card"; self.card.mkdir()
        self.settings = self.home / "settings.json"
        self.settings.write_text(json.dumps({"welcomed": True, "folder": str(self.vbox)}))
        self.patch_directory = patch("goprovbox.gui.data_directory", return_value=self.home)
        self.patch_directory.start(); self.addCleanup(self.patch_directory.stop)
        self.root = tk.Tk(); self.root.withdraw()
        self.addCleanup(self.destroy_root)

    def destroy_root(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def app(self, **kwargs):
        app = App(self.root, **kwargs)
        self.addCleanup(app.temporary.cleanup)
        return app

    def test_legacy_settings_keep_shared_folder_and_recursive_card_default(self):
        self.settings.write_text(json.dumps({"welcomed": True, "folder": str(self.vbox),
                                             "recursive_gopro": False}))
        app = self.app()
        self.assertTrue(app._gopro_folder_automatic)
        self.assertTrue(app.recursive_gopro.get())
        self.assertEqual(app.gopro_folder.get(), str(self.vbox))
        self.assertTrue(app.gopro_entry.instate(["!disabled"]))
        self.assertFalse(hasattr(app, "same_gopro_check"))
        self.assertFalse(hasattr(app, "recursive_gopro_check"))
        self.assertEqual(app.options_menu.type("Include GoPro subfolders"), "checkbutton")
        self.assertEqual(str(app.options_menu.entrycget("Include GoPro subfolders", "variable")),
                         str(app.recursive_gopro))
        widgets = [self.root]
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            if "text" in widget.keys():
                text = str(widget.cget("text")).casefold()
                self.assertNotIn("include gopro subfolders", text)
                self.assertNotIn("clear gopro videos", text)
        self.assertEqual(Path(app.output.get()).parent, self.vbox)
        for name in ("Other runs", "Third run"):
            another = self.home / name; another.mkdir()
            app.folder.set(str(another))
            self.assertEqual(app.gopro_folder.get(), str(another))
            self.assertEqual(Path(app.output.get()).parent, another)
            self.assertTrue(app._gopro_folder_automatic)

    def test_first_vbox_choice_fills_an_unset_gopro_source(self):
        self.settings.write_text(json.dumps({"welcomed": True}))
        app = self.app()
        self.assertEqual(app.gopro_folder.get(), "")
        app.folder.set(str(self.vbox))
        self.assertEqual(app.gopro_folder.get(), str(self.vbox))
        self.assertTrue(app._gopro_folder_automatic)

    def test_explicit_source_overrides_preferences_and_busy_locks_all_controls(self):
        self.settings.write_text(json.dumps({"welcomed": True, "folder": str(self.vbox),
                                             "same_gopro_folder": True, "gopro_folder": str(self.vbox),
                                             "include_gopro_subfolders": True}))
        app = self.app(gopro_folder=self.card, recursive_gopro=False)
        self.assertFalse(app._gopro_folder_automatic)
        self.assertFalse(app.recursive_gopro.get())
        self.assertEqual(app.gopro_folder.get(), str(self.card))
        self.assertTrue(app.gopro_entry.instate(["!disabled"]))
        for busy in (True, False):
            app.set_busy(busy)
            for control in (app.gopro_entry, app.gopro_browse):
                self.assertEqual(control.instate(["disabled"]), busy)
        another = self.home / "Other runs"; another.mkdir()
        app.folder.set(str(another))
        self.assertEqual(app.gopro_folder.get(), str(self.card))

    def test_typing_a_separate_source_stops_following_the_vbox_folder(self):
        app = self.app()
        app.gopro_folder.set(str(self.card))
        another = self.home / "Other runs"; another.mkdir()
        app.folder.set(str(another))
        self.assertEqual(app.gopro_folder.get(), str(self.card))
        self.assertFalse(app._gopro_folder_automatic)

    def test_explicitly_browsing_to_the_current_default_is_still_a_user_choice(self):
        app = self.app()
        with patch("goprovbox.gui.filedialog.askdirectory", return_value=str(self.vbox)):
            app.choose_gopro_folder()
        another = self.home / "Other runs"; another.mkdir()
        app.folder.set(str(another))
        self.assertEqual(app.gopro_folder.get(), str(self.vbox))
        self.assertFalse(app._gopro_folder_automatic)

    def test_cancelled_gopro_browse_keeps_the_existing_source_and_scan(self):
        app = self.app(gopro_folder=self.card)
        original_scan = object(); app.scan = original_scan
        with patch("goprovbox.gui.filedialog.askdirectory", return_value=""):
            app.choose_gopro_folder()
        self.assertEqual(app.gopro_folder.get(), str(self.card))
        self.assertFalse(app._gopro_folder_automatic)
        self.assertIs(app.scan, original_scan)

    def test_source_controls_and_export_actions_fit_minimum_window(self):
        app = self.app(gopro_folder=self.card)
        self.root.deiconify(); self.root.geometry("880x740"); self.root.update()
        self.assertEqual(app.scan_button.cget("text"), "Scan for matches")
        for entry in (app.source_entry, app.gopro_entry):
            self.assertGreaterEqual(app.scan_button.winfo_rooty(),
                                    entry.winfo_rooty() + entry.winfo_height())
        self.assertAlmostEqual(app.browse.winfo_rootx() + app.browse.winfo_width(),
                               app.gopro_browse.winfo_rootx() + app.gopro_browse.winfo_width(), delta=2)
        tab_order = (app.source_entry, app.browse, app.gopro_entry, app.gopro_browse,
                     app.scan_button)
        for current, following in zip(tab_order, tab_order[1:]):
            self.assertEqual(current.tk_focusNext(), following)
        for full_scene in (False, True):
            with self.subTest(full_scene=full_scene):
                if full_scene:
                    app.overlay_mode.set("Full scene · no rear camera"); app.overlay_changed()
                    app.crop_note.set("VBOX 16:9 · no crop")
                    app.run_detail.set("Recorded 30 Oct 2026, 09:20:10\n"
                                       "Run best 1:02.440 · VBOX0001.vbo\n"
                                       "★ This clip has fastest-lap footage")
                self.root.update_idletasks()
                controls = (app.source_entry, app.browse, app.gopro_entry, app.gopro_browse,
                            app.scan_button, app.preview_label,
                            app.overlay_preview, app.create, app.open_result)
                if full_scene:
                    controls += (app.scene_entry, app.scene_button)
                for control in controls:
                    self.assertGreater(control.winfo_width(), 1)
                    self.assertGreater(control.winfo_height(), 1)
                    self.assertGreaterEqual(control.winfo_rootx(), self.root.winfo_rootx())
                    self.assertGreaterEqual(control.winfo_rooty(), self.root.winfo_rooty())
                    self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                         self.root.winfo_rootx() + self.root.winfo_width())
                    self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                         self.root.winfo_rooty() + self.root.winfo_height())
                self.assertGreaterEqual(app.tree.winfo_height(), 150)
                self.assertGreaterEqual(app.preview_label.winfo_height(), 64)

    def test_separate_source_preferences_round_trip(self):
        app = self.app(gopro_folder=self.card)
        app.options_menu.invoke("Include GoPro subfolders")
        app.save_preferences()
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved["gopro_folder"], str(self.card))
        self.assertNotIn("same_gopro_folder", saved)
        self.assertNotIn("recursive_gopro", saved)
        self.assertFalse(saved["include_gopro_subfolders"])
        # Restoring source fields is independent of the window lifecycle.
        another = tk.Toplevel(self.root)
        restored = App(another)
        self.addCleanup(restored.temporary.cleanup)
        self.assertFalse(restored._gopro_folder_automatic)
        self.assertEqual(restored.gopro_folder.get(), str(self.card))
        self.assertFalse(restored.recursive_gopro.get())
        another.destroy()

    def test_automatic_source_preferences_round_trip_without_storing_the_mirrored_path(self):
        app = self.app()
        app.save_preferences()
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved["gopro_folder"], "")
        self.assertNotIn("same_gopro_folder", saved)
        self.assertNotIn("recursive_gopro", saved)
        self.assertTrue(saved["include_gopro_subfolders"])
        another_folder = self.home / "Another run"; another_folder.mkdir()
        another_window = tk.Toplevel(self.root)
        restored = App(another_window, folder=another_folder)
        self.addCleanup(restored.temporary.cleanup)
        self.assertTrue(restored._gopro_folder_automatic)
        self.assertEqual(restored.gopro_folder.get(), str(another_folder))
        self.assertTrue(restored.recursive_gopro.get())
        another_window.destroy()

    def test_subfolder_menu_choice_discards_matches_and_locks_while_busy(self):
        app = self.app(gopro_folder=self.card)
        app.scan = object()
        app.options_menu.invoke("Include GoPro subfolders")
        self.assertFalse(app.recursive_gopro.get())
        self.assertIsNone(app.scan)
        app.set_busy(True)
        self.assertTrue(app.options_button.instate(["!disabled"]))
        self.assertEqual(app.options_menu.entrycget("Include GoPro subfolders", "state"), "disabled")
        for label in ("Details…", "What do red stars mean?…"):
            self.assertEqual(app.options_menu.entrycget(label, "state"), "normal")
        app.options_menu.invoke("Include GoPro subfolders")
        self.assertFalse(app.recursive_gopro.get())
        app.set_busy(False)
        self.assertEqual(app.options_menu.entrycget("Include GoPro subfolders", "state"), "normal")
        app.options_menu.invoke("Include GoPro subfolders")
        self.assertTrue(app.recursive_gopro.get())

    def test_old_shared_folder_preference_migrates_to_the_new_vbox_folder(self):
        self.settings.write_text(json.dumps({"welcomed": True, "folder": str(self.vbox),
                                             "same_gopro_folder": True, "gopro_folder": str(self.vbox)}))
        another = self.home / "New recordings"; another.mkdir()
        app = self.app(folder=another)
        self.assertTrue(app._gopro_folder_automatic)
        self.assertEqual(app.gopro_folder.get(), str(another))

    def test_old_separate_folder_preference_keeps_the_custom_source(self):
        self.settings.write_text(json.dumps({"welcomed": True, "folder": str(self.vbox),
                                             "same_gopro_folder": False, "gopro_folder": str(self.card)}))
        another = self.home / "New recordings"; another.mkdir()
        app = self.app(folder=another)
        self.assertFalse(app._gopro_folder_automatic)
        self.assertEqual(app.gopro_folder.get(), str(self.card))

    def test_clearing_the_source_uses_the_vbox_folder_when_scanning(self):
        app = self.app(gopro_folder=self.card)
        app.gopro_folder.set("")
        with patch("goprovbox.gui.tools_ready", return_value=True), patch.object(app, "worker") as worker:
            app.begin_scan()
        self.assertTrue(app._gopro_folder_automatic)
        self.assertEqual(app.gopro_folder.get(), str(self.vbox))
        with patch("goprovbox.gui.scan_folder", return_value="scanned") as scanner:
            worker.call_args.args[0]()
        self.assertEqual(scanner.call_args.args[0], self.vbox)
        self.assertEqual(scanner.call_args.kwargs["gopro_folder"], self.vbox)

    def test_clearing_the_source_resumes_following_the_vbox_folder(self):
        app = self.app(gopro_folder=self.card)
        app.gopro_folder.set("")
        another = self.home / "Other runs"; another.mkdir()
        app.folder.set(str(another))
        self.assertTrue(app._gopro_folder_automatic)
        self.assertEqual(app.gopro_folder.get(), str(another))

    def test_source_changes_discard_stale_matches_but_keep_custom_output(self):
        app = self.app(gopro_folder=self.card)
        custom = str(self.home / "Finished clips")
        app.output.set(custom)
        for variable, value in ((app.gopro_folder, str(self.home)), (app.recursive_gopro, False),
                                (app.gopro_folder, "")):
            with self.subTest(variable=variable):
                app.scan = object(); app.result = self.home / "old result"
                app.included_videos.add("clip.mp4"); app.crops["clip.mp4"] = "top"
                app.tree.insert("", "end", iid="0", values=("yes", "clip.mp4", "run.vbo", "1 min"))
                generation = app.preview_generation
                variable.set(value)
                self.assertIsNone(app.scan); self.assertIsNone(app.result)
                self.assertFalse(app.included_videos); self.assertFalse(app.crops)
                self.assertFalse(app.tree.get_children())
                self.assertGreater(app.preview_generation, generation)
                self.assertTrue(app.create.instate(["disabled"]))
                self.assertTrue(app.open_result.instate(["disabled"]))
                self.assertEqual(app.output.get(), custom)

    def test_scan_job_captures_both_sources_and_recursion_before_worker_runs(self):
        app = self.app(gopro_folder=self.card, recursive_gopro=True)
        with patch("goprovbox.gui.tools_ready", return_value=True), patch.object(app, "worker") as worker:
            app.begin_scan()
        job = worker.call_args.args[0]
        app.gopro_folder.set(str(self.home)); app.recursive_gopro.set(False)
        with patch("goprovbox.gui.scan_folder", return_value="scanned") as scanner:
            job()
        self.assertEqual(scanner.call_args.args[0], self.vbox)
        self.assertEqual(scanner.call_args.kwargs["gopro_folder"], self.card)
        self.assertTrue(scanner.call_args.kwargs["recursive_gopro"])

    def test_missing_card_has_actionable_error_without_starting_scan(self):
        app = self.app(gopro_folder=self.home / "Removed card")
        with patch("goprovbox.gui.tools_ready", return_value=True), \
             patch("goprovbox.gui.messagebox.showerror") as error, patch.object(app, "worker") as worker:
            app.begin_scan()
        self.assertEqual(error.call_args.args[0], "Choose GoPro videos")
        worker.assert_not_called()

    def test_demo_resets_card_to_bundled_shared_folder(self):
        app = self.app(gopro_folder=self.card)
        demo = self.home / "Practice recordings"; demo.mkdir()
        with patch("goprovbox.gui.tools_ready", return_value=True), \
             patch("goprovbox.gui.copy_demo", return_value=demo), patch.object(app, "begin_scan") as scan:
            app.try_demo()
        self.assertTrue(app._gopro_folder_automatic)
        self.assertEqual(app.folder.get(), str(demo))
        self.assertEqual(app.gopro_folder.get(), str(demo))
        self.assertEqual(Path(app.output.get()).parent, demo)
        scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
