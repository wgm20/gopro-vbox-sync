"""Help actions must not uninstall another copy or bypass video-tools consent."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch
import os
import queue
import unittest

from goprovbox import distribution as d
from goprovbox.gui import App


@unittest.skipUnless(os.name == "nt", "Windows installation layout")
class InstalledCopyTests(unittest.TestCase):
    def test_uninstaller_belongs_to_running_copy_and_keeps_confirmation(self):
        with TemporaryDirectory(prefix="app install ") as temp:
            folder = Path(temp).resolve()
            installed = folder / "Installed app"; installed.mkdir()
            portable = folder / "Portable copy"; portable.mkdir()
            for name in ("GoProVBOXSync.exe", "unins000.exe", "unins000.dat"):
                (installed / name).touch()
            (portable / "GoProVBOXSync.exe").touch()
            with patch.object(d.sys, "frozen", True, create=True):
                with patch.object(d.sys, "executable", str(installed / "GoProVBOXSync.exe")):
                    self.assertEqual(d.uninstall_command(), [str(installed / "unins000.exe"), "/NORESTART"])
                    (installed / "unins000.dat").unlink()
                    self.assertIsNone(d.uninstall_command())
                with patch.object(d.sys, "executable", str(portable / "GoProVBOXSync.exe")):
                    self.assertIsNone(d.uninstall_command())
            with patch.object(d.sys, "frozen", False, create=True), patch.object(d.sys, "executable", str(installed / "GoProVBOXSync.exe")):
                self.assertIsNone(d.uninstall_command())


class HelpActionTests(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.root = Mock(); app.busy = False; app.closing = False; app.scan = None
        app.finish_close = Mock(); app.set_busy = Mock()
        app.status = Mock(); app.progress = {}; app.cancel = Event()
        app.events = queue.Queue(); app.worker = Mock()
        return app

    def test_uninstall_launches_confirmation_then_closes_app(self):
        app = self.app(); order = []
        command = [str(Path("Installed app/unins000.exe").resolve()), "/NORESTART"]
        app.finish_close.side_effect = lambda: order.append("close")
        with patch("goprovbox.gui.uninstall_command", return_value=command), patch("goprovbox.gui.subprocess.Popen") as start:
            start.side_effect = lambda *args, **kwargs: order.append("uninstaller")
            app.uninstall()
        start.assert_called_once_with(command, cwd=str(Path(command[0]).parent), close_fds=True)
        self.assertEqual(order, ["uninstaller", "close"])

    def test_busy_or_portable_copy_never_starts_uninstall(self):
        for busy, command in ((True, ["unins000.exe"]), (False, None)):
            with self.subTest(busy=busy):
                app = self.app(); app.busy = busy
                with patch("goprovbox.gui.uninstall_command", return_value=command), patch("goprovbox.gui.subprocess.Popen") as start, patch("goprovbox.gui.messagebox.showinfo") as info:
                    app.uninstall()
                start.assert_not_called(); app.finish_close.assert_not_called(); info.assert_called_once()

    def test_failed_uninstaller_launch_leaves_app_open(self):
        app = self.app()
        with patch("goprovbox.gui.uninstall_command", return_value=["unins000.exe", "/NORESTART"]), patch("goprovbox.gui.subprocess.Popen", side_effect=OSError("missing")), patch("goprovbox.gui.messagebox.showerror") as error:
            app.uninstall()
        app.finish_close.assert_not_called(); error.assert_called_once()

    def test_missing_tools_pause_scan_for_setup(self):
        app = self.app(); app.setup_tools = Mock()
        with patch("goprovbox.gui.tools_ready", return_value=False), patch("goprovbox.gui.scan_folder") as scan:
            app.begin_scan()
        app.setup_tools.assert_called_once_with(app.begin_scan)
        scan.assert_not_called(); app.worker.assert_not_called()

    def test_declining_tools_download_does_not_start_work(self):
        app = self.app(); after = Mock()
        with patch("goprovbox.gui.tools_ready", return_value=False), patch("goprovbox.gui.messagebox.askokcancel", return_value=False), patch("goprovbox.gui.download_tools") as download:
            app.setup_tools(after)
        download.assert_not_called(); after.assert_not_called(); app.worker.assert_not_called()

    def test_completed_tools_download_resumes_requested_action_on_ui_thread(self):
        app = self.app(); after = Mock()
        with patch("goprovbox.gui.tools_ready", return_value=False), patch("goprovbox.gui.messagebox.askokcancel", return_value=True), patch("goprovbox.gui.download_tools") as download:
            app.setup_tools(after)
            app.worker.call_args.args[0]()
        download.assert_called_once(); after.assert_not_called()
        app.events.put(("idle", None)); app.poll()
        app.root.after.assert_any_call(0, after)


if __name__ == "__main__":
    unittest.main()
