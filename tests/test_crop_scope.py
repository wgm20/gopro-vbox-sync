"""Framing and checked export scope stay clear when different files are previewed."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import threading
import tkinter as tk
import unittest

from PIL import Image

from goprovbox.crop import Reference
from goprovbox.engine import Scan, intersections
from goprovbox.framing import CropEditor, FramingPreview
from goprovbox.gui import App
from tests.test_core import fixture, video


class CropScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        (self.folder / 'settings.json').write_text(json.dumps({'welcomed': True, 'folder': str(self.folder)}))
        settings = patch('goprovbox.gui.data_directory', return_value=self.folder)
        settings.start(); self.addCleanup(settings.stop)
        decode = patch('goprovbox.gui.preview', side_effect=self.synthetic_frame)
        decode.start(); self.addCleanup(decode.stop)
        self.initial_threads = {thread.ident for thread in threading.enumerate()}
        self.root = tk.Tk(); self.root.withdraw(); self.addCleanup(self.cleanup_root)
        self.app = App(self.root)
        self.vbo = fixture(self.folder / 'VBOX0003.vbo')
        self.clips = [replace(video(self.folder / name, duration=3), width=160, height=160)
                      for name in ('GX010548.MP4', 'GX020548.MP4')]
        self.app.scan = Scan(self.folder, self.clips, [self.vbo],
                             [match for clip in self.clips for match in intersections(self.vbo, clip)],
                             [], [], {}, crop_references={clip.path.name: Reference(Fraction(16, 9), {})
                                                        for clip in self.clips})
        self.app.included_videos = {self.clips[0].path.name}
        self.app.refresh_rows()

    def synthetic_frame(self, path, seconds, rotation, destination, *, crop=None, output_size=None):
        Image.new('RGB', output_size or (160, 160), 'white').save(destination)

    def cleanup_root(self):
        for thread in threading.enumerate():
            if thread.ident not in self.initial_threads:
                thread.join(timeout=2)
        for window in self.root.winfo_children():
            if isinstance(window, tk.Toplevel):
                window.destroy()
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()
        if hasattr(self, 'app'):
            self.app.temporary.cleanup()

    def select(self, row):
        self.app.tree.selection_set(str(row))
        self.app.select_video()

    def apply_second_file_crop(self):
        self.select(1)
        prepared = FramingPreview(self.clips[1], 0, Fraction(16, 9), Image.new('RGB', (160, 160)),
                                  {False: (160, 160), True: (160, 90)}, {}, 1)
        self.app.events.put(('framing_preview', (prepared, 'none', self.app.scan)))
        self.app.poll()
        editors = [window for window in self.root.winfo_children() if isinstance(window, CropEditor)]
        self.assertEqual(len(editors), 1)
        editor = editors[0]; editor.withdraw()
        widgets = [editor]; labels = []
        while widgets:
            widget = widgets.pop(); widgets.extend(widget.winfo_children())
            if 'text' in widget.keys():
                labels.append(str(widget.cget('text')))
        self.assertIn(self.clips[1].path.name, '\n'.join(labels))
        self.assertIn('Framing applies to this video', '\n'.join(labels))
        editor.enabled.set(True)
        editor.position = {'mode': 'custom', 'x': .5, 'y': .2}
        editor.commit()
        return deepcopy(self.app.crops[self.clips[1].path.name])

    def test_preview_header_tracks_file_and_checked_export_scope(self):
        self.select(1)
        self.assertIn(self.clips[1].path.name, self.app.preview_title.get())
        self.assertIn('not included', self.app.preview_title.get().casefold())
        self.app.toggle_include('1')
        self.assertIn('included', self.app.preview_title.get().casefold())
        self.assertNotIn('not included', self.app.preview_title.get().casefold())
        self.select(0)
        self.assertIn(self.clips[0].path.name, self.app.preview_title.get())
        self.app.toggle_include('0')
        self.assertIn('not included', self.app.preview_title.get().casefold())

    def test_checkbox_click_and_space_preview_the_file_the_user_toggled(self):
        self.root.deiconify(); self.root.geometry('880x740'); self.root.update()
        self.select(0); self.app.tree.focus('0')
        x, y, width, height = self.app.tree.bbox('1', 'include')
        event = SimpleNamespace(x=x + width // 2, y=y + height // 2)
        self.app.click_include(event)
        self.assertIs(self.app.selected_video(), self.clips[1])
        self.assertEqual(self.app.tree.focus(), '1')
        self.assertEqual(self.app.preview_title.get(), self.clips[1].path.name + ' · Included')
        self.app.tree.focus('0')
        self.app.toggle_focused_include()
        self.assertIs(self.app.selected_video(), self.clips[0])
        self.assertEqual(self.app.tree.focus(), '0')
        self.assertEqual(self.app.preview_title.get(), self.clips[0].path.name + ' · Not included')
        included = set(self.app.included_videos)
        self.app.set_busy(True)
        self.app.click_include(event)
        self.assertEqual(self.app.included_videos, included)
        self.assertIs(self.app.selected_video(), self.clips[0])
        self.assertEqual(self.app.tree.focus(), '0')

    def test_declining_uncropped_export_keeps_crop_and_inclusion_unchanged(self):
        choice = self.apply_second_file_crop()
        with patch('goprovbox.gui.messagebox.askyesno', return_value=False) as confirm, \
                patch.object(self.app, 'worker') as worker:
            self.app.begin_export()
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[0], 'Export without the saved crop?')
        self.assertEqual(str(confirm.call_args.kwargs['default']), 'no')
        self.assertIn(self.clips[0].path.name, confirm.call_args.args[1])
        self.assertIn(self.clips[1].path.name, confirm.call_args.args[1])
        worker.assert_not_called()
        self.assertEqual(self.app.included_videos, {self.clips[0].path.name})
        self.assertEqual(self.app.crops, {self.clips[1].path.name: choice})

    def test_accepting_uncropped_export_preserves_checked_files(self):
        self.apply_second_file_crop()
        with patch('goprovbox.gui.messagebox.askyesno', return_value=True) as confirm, \
                patch.object(self.app, 'worker') as worker:
            self.app.begin_export()
        confirm.assert_called_once()
        self.assertEqual(self.app.included_videos, {self.clips[0].path.name})
        with patch('goprovbox.gui.export', return_value=self.folder / 'out') as dispatch:
            worker.call_args.args[0]()
        settings = dispatch.call_args.kwargs
        self.assertEqual(settings['include_videos'], {self.clips[0].path.name})
        self.assertNotIn(self.clips[0].path.name, settings['crops'])

    def test_included_file_crop_survives_preview_change_sorting_and_export_snapshot(self):
        choice = self.apply_second_file_crop()
        self.app.toggle_include('1'); self.app.toggle_include('0')
        self.app.sort_rows('video', True)
        self.select(0)
        with patch('goprovbox.gui.messagebox.askyesno') as confirm, patch.object(self.app, 'worker') as worker:
            self.app.begin_export()
        confirm.assert_not_called()
        self.app.crops[self.clips[1].path.name]['y'] = .8
        self.app.included_videos.clear()
        with patch('goprovbox.gui.export', return_value=self.folder / 'out') as dispatch:
            worker.call_args.args[0]()
        self.assertEqual(dispatch.call_args.kwargs['include_videos'], {self.clips[1].path.name})
        self.assertEqual(dispatch.call_args.kwargs['crops'], {self.clips[1].path.name: choice})

    def test_export_without_any_saved_crop_does_not_request_confirmation(self):
        for choices in ({}, {self.clips[1].path.name: 'none'}):
            with self.subTest(choices=choices), patch('goprovbox.gui.messagebox.askyesno') as confirm, \
                    patch.object(self.app, 'worker') as worker:
                self.app.crops = choices
                self.app.begin_export()
                confirm.assert_not_called()
                worker.assert_called_once()


if __name__ == '__main__':
    unittest.main()
