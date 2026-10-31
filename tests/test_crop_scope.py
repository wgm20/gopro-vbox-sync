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

from goprovbox.crop import Reference, rectangle
from goprovbox.engine import Scan, dimensions, intersections
from goprovbox.framing import CropEditor, FramingPreview
from goprovbox.gpmf import TelemetryError
from goprovbox.gui import App
from tests.test_core import fixture, video


class CropScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        (self.folder / 'settings.json').write_text(json.dumps({'welcomed': True, 'folder': str(self.folder)}))
        settings = patch('goprovbox.gui.data_directory', return_value=self.folder)
        settings.start(); self.addCleanup(settings.stop)
        decode = patch('goprovbox.framing.prepare_output_preview', side_effect=self.synthetic_frame)
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
        self.app.set_busy(False)

    def synthetic_frame(self, video, matches, rotation, max_size, aspect, mode, scene_path,
                        destination, cancel, choice):
        size = dimensions(video, rotation, max_size, rectangle(video.width, video.height, rotation, aspect, choice))
        Image.new('RGB', size, 'white').save(destination)
        return size

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

    def choose_export(self, row):
        self.root.update()
        self.app.export_video_combo.set(self.clips[row].path.name)
        self.app.export_video_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertIs(self.app.export_video(), self.clips[row])

    def open_editor(self, row):
        prepared = FramingPreview(self.clips[row], 0, Fraction(16, 9), Image.new('RGB', (160, 160)),
                                  {False: (160, 160), True: (160, 90)}, {}, 1)
        self.app.events.put(('framing_preview', (prepared, deepcopy(self.app.crop_choice), self.app.scan)))
        self.app.poll()
        editors = [window for window in self.root.winfo_children() if isinstance(window, CropEditor)]
        self.assertEqual(len(editors), 1)
        editor = editors[0]; editor.withdraw()
        widgets = [editor]; labels = []
        while widgets:
            widget = widgets.pop(); widgets.extend(widget.winfo_children())
            if 'text' in widget.keys():
                labels.append(str(widget.cget('text')))
        self.assertIn(self.clips[row].path.name, '\n'.join(labels))
        self.assertIn('Crop applies to all selected videos', '\n'.join(labels))
        self.assertIn('Apply to selected videos', labels)
        return editor

    def apply_shared_crop(self, row=1):
        if self.clips[row].path.name not in self.app.included_videos:
            self.app.toggle_include(str(row))
        self.choose_export(row)
        editor = self.open_editor(row)
        editor.enabled.set(True)
        editor.position = {'mode': 'custom', 'x': .5, 'y': .2}
        editor.commit()
        return deepcopy(self.app.crop_choice)

    def test_excluded_highlight_does_not_change_the_export_preview_or_frame_target(self):
        self.select(1)
        self.assertIs(self.app.selected_video(), self.clips[1])
        self.assertIs(self.app.export_video(), self.clips[0])
        self.assertEqual(tuple(self.app.export_video_combo.cget('values')), (self.clips[0].path.name,))
        self.assertEqual(self.app.export_video_name.get(), self.clips[0].path.name)
        prepared = FramingPreview(self.clips[0], 0, Fraction(16, 9), Image.new('RGB', (160, 160)),
                                  {False: (160, 160), True: (160, 90)}, {}, 1)
        with patch.object(self.app, 'worker') as worker, \
                patch('goprovbox.framing.prepare_preview', return_value=prepared) as prepare:
            self.app.preview_overlay()
            worker.call_args.args[0]()
        self.assertIs(prepare.call_args.args[0], self.clips[0])

    def test_export_choice_lists_only_checked_files_and_retargets_when_unchecked(self):
        unmatched = video(self.folder / 'No matching run.MP4', self.clips[0].clock.utc(0) + 100, 3)
        self.app.scan.videos = self.clips + [unmatched]
        self.app.included_videos.add(unmatched.path.name)
        self.app.refresh_rows()
        self.assertNotIn(unmatched.path.name, self.app.export_video_combo.cget('values'))
        self.app.toggle_include('1')
        self.assertEqual(set(self.app.export_video_combo.cget('values')), {clip.path.name for clip in self.clips})
        self.choose_export(1)
        self.select(0)
        self.assertIs(self.app.export_video(), self.clips[0])
        self.choose_export(1)
        self.app.toggle_include('1')
        self.assertIs(self.app.export_video(), self.clips[0])
        self.assertEqual(tuple(self.app.export_video_combo.cget('values')), (self.clips[0].path.name,))
        self.assertEqual(self.app.export_video_name.get(), self.clips[0].path.name)
        self.app.toggle_include('0')
        for thread in threading.enumerate():
            if thread.ident not in self.initial_threads:
                thread.join(timeout=2)
        self.app.poll()
        self.assertIsNone(self.app.export_video())
        self.assertEqual(self.app.export_video_name.get(), '')
        self.assertEqual(tuple(self.app.export_video_combo.cget('values')), ())
        self.assertTrue(self.app.overlay_preview.instate(['disabled']))
        self.assertTrue(self.app.rotate_combo.instate(['disabled']))
        self.assertIsNone(self.app.preview_source)
        self.assertFalse(self.app.preview_label.cget('image'))
        with patch.object(self.app, 'worker') as worker:
            self.app.preview_overlay()
        worker.assert_not_called()

    def test_checkbox_click_and_space_keep_preview_inside_export_scope(self):
        self.root.deiconify(); self.root.geometry('880x740'); self.root.update()
        self.select(0); self.app.tree.focus('0')
        x, y, width, height = self.app.tree.bbox('1', 'include')
        event = SimpleNamespace(x=x + width // 2, y=y + height // 2)
        self.app.click_include(event)
        self.assertIs(self.app.selected_video(), self.clips[1])
        self.assertEqual(self.app.tree.focus(), '1')
        self.assertIn(self.app.export_video().path.name, self.app.included_videos)
        self.choose_export(1)
        self.app.tree.focus('0')
        self.app.toggle_focused_include()
        self.assertIs(self.app.selected_video(), self.clips[0])
        self.assertEqual(self.app.tree.focus(), '0')
        self.assertIs(self.app.export_video(), self.clips[1])
        self.assertEqual(self.app.export_video_name.get(), self.clips[1].path.name)
        included = set(self.app.included_videos)
        self.app.set_busy(True)
        self.app.click_include(event)
        self.assertEqual(self.app.included_videos, included)
        self.assertIs(self.app.selected_video(), self.clips[0])
        self.assertEqual(self.app.tree.focus(), '0')

    def test_rotation_changes_only_the_export_target_and_none_when_empty(self):
        self.select(1)
        self.app.rotation.set('90° clockwise'); self.app.change_rotation()
        self.assertEqual(self.app.rotations, {self.clips[0].path.name: 90})
        self.app.toggle_include('0')
        self.app.rotation.set('180°'); self.app.change_rotation()
        self.assertEqual(self.app.rotations, {self.clips[0].path.name: 90})

    def test_editor_cannot_save_to_a_file_removed_from_export_scope(self):
        self.app.toggle_include('1'); self.choose_export(1)
        editor = self.open_editor(1)
        self.app.toggle_include('1')
        editor.enabled.set(True); editor.position = {'mode': 'custom', 'x': .5, 'y': .2}
        with patch('goprovbox.gui.messagebox.showinfo'), patch('goprovbox.gui.messagebox.showwarning'):
            editor.commit()
        self.assertEqual(self.app.crop_choice, 'none')
        self.assertEqual(self.app.included_videos, {self.clips[0].path.name})

    def test_stale_editor_cannot_apply_after_the_export_target_changes(self):
        self.app.toggle_include('1'); self.choose_export(1)
        editor = self.open_editor(1)
        self.choose_export(0)
        editor.enabled.set(True); editor.position = {'mode': 'custom', 'x': .5, 'y': .2}
        with patch('goprovbox.gui.messagebox.showinfo'), patch('goprovbox.gui.messagebox.showwarning'):
            editor.commit()
        self.assertEqual(self.app.crop_choice, 'none')
        self.assertIs(self.app.export_video(), self.clips[0])
        self.assertEqual(self.app.included_videos, {clip.path.name for clip in self.clips})

    def test_visual_options_render_a_new_export_photo_with_the_current_snapshot(self):
        app = self.app

        def settle_previews():
            for thread in threading.enumerate():
                if thread.ident not in self.initial_threads:
                    thread.join(timeout=2)
            app.poll()

        settle_previews()
        clip = self.clips[0]; clip.width = clip.height = 1600

        def rendered_frame(video, matches, rotation, max_size, aspect, mode, scene_path,
                           destination, cancel, choice):
            size = dimensions(video, rotation, max_size,
                              rectangle(video.width, video.height, rotation, aspect, choice))
            colour = (180, 40, 70) if mode == 'full' else (20, 40, 100)
            Image.new('RGB', size, colour).save(destination)
            return size

        with patch('goprovbox.framing.prepare_output_preview', side_effect=rendered_frame) as render:
            app.preview_request = None; app.refresh_export_preview(); settle_previews()
            self.assertEqual(app.preview_source.size, (1600, 1600))
            self.assertEqual(app.preview_source.getpixel((0, 0)), (20, 40, 100))
            self.assertEqual(app.preview_title.get(), 'Export preview · 1600 × 1600')
            choice = {'mode': 'custom', 'x': .5, 'y': .2}
            app.crop_choice = deepcopy(choice)
            scene_path = str(self.folder / 'Selected scene.gvscene')
            app.scene_path.set(scene_path)
            app.resolution_menu.invoke('Compact · 1280 px')
            app.overlay_menu.invoke('Full scene · no rear camera')
            app.rotation.set('90° clockwise'); app.change_rotation()
            generation = app.preview_generation
            settle_previews()
            current = [call for call in render.call_args_list
                       if Path(call.args[7]).name == f'frame-{generation}.png']
            self.assertEqual(len(current), 1)
            args = current[0].args
            self.assertIs(args[0], clip)
            self.assertTrue(all(match.video is clip for match in args[1]))
            self.assertEqual(args[2:7], (90, 1280, Fraction(16, 9), 'full', scene_path))
            self.assertEqual(args[9], choice)
            self.assertEqual(app.preview_source.size, (1280, 720))
            self.assertEqual(app.preview_source.getpixel((0, 0)), (180, 40, 70))
            self.assertEqual(app.preview_title.get(), 'Export preview · 1280 × 720')
            call_count = render.call_count
            self.select(1); self.root.update(); settle_previews()
            self.assertIs(app.export_video(), clip)
            self.assertEqual(render.call_count, call_count)
            self.assertEqual(app.preview_source.size, (1280, 720))
            self.assertEqual(app.preview_source.getpixel((0, 0)), (180, 40, 70))

    def test_shared_crop_exports_all_checked_files_with_an_immutable_snapshot(self):
        choice = self.apply_shared_crop()
        self.app.sort_rows('video', True)
        self.select(0)
        with patch('goprovbox.gui.messagebox.askyesno') as confirm, patch.object(self.app, 'worker') as worker:
            self.app.begin_export()
        confirm.assert_not_called()
        self.app.crop_choice['y'] = .8
        self.app.included_videos.clear()
        with patch('goprovbox.gui.export', return_value=self.folder / 'out') as dispatch:
            worker.call_args.args[0]()
        self.assertEqual(dispatch.call_args.kwargs['include_videos'], {clip.path.name for clip in self.clips})
        self.assertEqual(dispatch.call_args.kwargs['crops'], {clip.path.name: choice for clip in self.clips})

    def test_newly_checked_video_inherits_the_same_crop(self):
        choice = self.apply_shared_crop(0)
        self.assertEqual(self.app.included_videos, {self.clips[0].path.name})
        self.app.toggle_include('1')
        self.choose_export(1)
        self.assertEqual(self.app.crop_choice, choice)
        self.assertEqual(self.app.video_crop(self.clips[0], 0), self.app.video_crop(self.clips[1], 0))
        self.assertIsNotNone(self.app.video_crop(self.clips[1], 0))
        expected = self.app.video_crop(self.clips[0], 0)
        self.app.toggle_include('0'); self.app.toggle_include('1')
        self.assertIsNone(self.app.export_video())
        self.assertEqual(self.app.crop_choice, choice)
        self.app.toggle_include('0')
        self.assertIs(self.app.export_video(), self.clips[0])
        self.assertEqual(self.app.crop_choice, choice)
        self.assertEqual(self.app.video_crop(self.clips[0], 0), expected)

    def test_disabling_crop_removes_it_from_every_checked_video(self):
        self.apply_shared_crop()
        editor = self.open_editor(1)
        self.assertTrue(editor.enabled.get())
        editor.enabled.set(False); editor.commit()
        self.assertEqual(self.app.crop_choice, 'none')
        for clip in self.clips:
            self.assertIsNone(self.app.video_crop(clip, 0))
        with patch('goprovbox.gui.messagebox.askyesno') as confirm, patch.object(self.app, 'worker') as worker:
            self.app.begin_export()
        confirm.assert_not_called()
        with patch('goprovbox.gui.export', return_value=self.folder / 'out') as dispatch:
            worker.call_args.args[0]()
        self.assertEqual(dispatch.call_args.kwargs['crops'], {})
        self.assertEqual(dispatch.call_args.kwargs['include_videos'], {clip.path.name for clip in self.clips})

    def test_shared_crop_blocks_edit_and_export_when_a_checked_video_has_no_reference(self):
        choice = self.apply_shared_crop()
        self.choose_export(0)
        for thread in threading.enumerate():
            if thread.ident not in self.initial_threads:
                thread.join(timeout=2)
        self.app.poll()
        self.assertIsNotNone(self.app.preview_source)
        self.app.scan.crop_references.pop(self.clips[1].path.name)
        message = 'Missing original VBOX video for ' + self.clips[1].path.name
        with patch('goprovbox.framing.prepare_output_preview', side_effect=TelemetryError(message)):
            self.choose_export(1)
            self.assertIsNone(self.app.preview_source)
            self.assertFalse(self.app.preview_label.cget('image'))
            for thread in threading.enumerate():
                if thread.ident not in self.initial_threads:
                    thread.join(timeout=2)
            self.app.poll()
        self.assertEqual(self.app.preview_title.get(), 'Export preview unavailable')
        self.assertIn(message, self.app.preview_label.cget('text'))
        self.assertIsNone(self.app.preview_source)
        self.assertFalse(self.app.preview_label.cget('image'))
        with patch('goprovbox.gui.messagebox.showerror') as error, patch.object(self.app, 'worker') as worker:
            self.app.preview_overlay()
        error.assert_called_once()
        worker.assert_not_called()
        self.assertEqual(self.app.crop_choice, choice)
        with patch('goprovbox.gui.messagebox.showerror') as error, patch.object(self.app, 'worker') as worker:
            self.app.begin_export()
        error.assert_called_once()
        self.assertIn(self.clips[1].path.name, error.call_args.args[1])
        worker.assert_not_called()
        self.assertEqual(self.app.crop_choice, choice)
        self.assertEqual(self.app.included_videos, {clip.path.name for clip in self.clips})


if __name__ == '__main__':
    unittest.main()
