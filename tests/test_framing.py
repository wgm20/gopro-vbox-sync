"""Editor interactions: exact framing, cancel/apply, resize, and GUI defaults."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tkinter as tk
import unittest

from PIL import Image

from goprovbox.framing import CropEditor, FramingPreview
from goprovbox.gui import App
from tests.test_core import video


class FramingInteractionTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.clip = replace(video(Path('GX010001.mp4')), width=1600, height=1600)
        self.prepared = FramingPreview(self.clip, 0, Fraction(16, 9), Image.new('RGB', (1600, 1600), 'white'),
                                       {False: (1600, 1600), True: (1600, 900)}, {}, 1)
        self.applied = Mock()
        self.editor = CropEditor(self.root, self.prepared, 'none', self.applied)
        self.editor.withdraw()

    def test_drag_uses_source_pixels_with_letterboxing_and_clamps(self):
        e = self.editor; e.enabled.set(True)
        # 400px preview of a 1600px source, with nonzero canvas margins.
        e.input_box = (25, 50, .25, .25)
        crop = self.prepared.crop(e.choice())
        start = SimpleNamespace(x=25 + 800*.25, y=50 + (crop.y+100)*.25)
        e.press(start)
        e.motion(SimpleNamespace(x=start.x, y=start.y-30))
        self.assertEqual(self.prepared.crop(e.choice()).y, 230)
        e.release(SimpleNamespace(x=start.x, y=-999))
        self.assertEqual(self.prepared.crop(e.choice()).y, 0)
        self.assertIsNone(e.drag)
        self.applied.assert_not_called()

    def test_apply_reopen_nudge_centre_and_disable(self):
        e = self.editor; e.enabled.set(True)
        e.nudge(0, -7)
        self.assertEqual(self.prepared.crop(e.choice()).y, 336)
        e.commit()
        choice = self.applied.call_args.args[0]
        self.editor = e = CropEditor(self.root, self.prepared, choice, self.applied)
        e.withdraw()
        self.assertEqual(self.prepared.crop(e.choice()).y, 336)
        e.centre(); self.assertEqual(self.prepared.crop(e.choice()).y, 350)
        e.enabled.set(False); e.commit()
        self.assertEqual(self.applied.call_args.args[0], 'none')

    def test_cancel_does_not_mutate_saved_choice_and_resize_does_not_change_it(self):
        e = self.editor; e.destroy()
        choice = {'mode': 'custom', 'x': .5, 'y': .3}
        self.editor = e = CropEditor(self.root, self.prepared, choice, self.applied)
        e.withdraw()
        original = e.choice()
        e.geometry('900x600'); self.root.update_idletasks()
        self.assertEqual(e.choice(), original)
        e.nudge(0, 9); e.destroy()
        self.assertEqual(choice, {'mode': 'custom', 'x': .5, 'y': .3})
        self.applied.assert_not_called()

    def test_missing_reference_keeps_uncropped_preview_available(self):
        self.editor.destroy()
        self.prepared.aspect = None
        self.editor = e = CropEditor(self.root, self.prepared, 'none', self.applied)
        e.withdraw()
        self.assertTrue(e.crop_toggle.instate(['disabled']))
        self.assertEqual(self.prepared.output(e.choice(), (400, 240)).size, (240, 240))
        e.commit(); self.applied.assert_called_once_with('none')

    def test_redrawing_at_different_window_sizes_keeps_source_framing(self):
        e = self.editor; e.enabled.set(True)
        e.position = {'mode': 'custom', 'x': .5, 'y': .3}
        original = e.choice()
        for width, height in ((503, 421), (277, 500), (600, 300)):
            with patch.object(e.input_canvas, 'winfo_width', return_value=width), \
                 patch.object(e.input_canvas, 'winfo_height', return_value=height), \
                 patch.object(e.output_canvas, 'winfo_width', return_value=width), \
                 patch.object(e.output_canvas, 'winfo_height', return_value=height):
                e.redraw()
                self.assertEqual(e.choice(), original)
                self.assertLessEqual(e.input_image.width(), width - 24)
                self.assertLessEqual(e.output_image.height(), height - 24)
                self.assertLess(abs(e.output_image.width() / e.output_image.height() - 16 / 9), .015)
                self.assertEqual(e.output_title.cget('text'), 'Output preview · 1600 × 900')


class RotationDefaultTests(unittest.TestCase):
    def test_selecting_video_ignores_detected_rotation_until_manually_changed(self):
        clip = video(Path('GX010001.mp4'))
        for detected in (90, 180, 270, None):
            clip.orientation = replace(clip.orientation, clockwise=detected)
            app = App.__new__(App)
            app.selected_video = Mock(return_value=clip)
            app.busy = False; app.rotations = {}; app.crops = {}
            app.rotation = Mock(); app.log = Mock(); app.load_preview = Mock()
            app.scan = SimpleNamespace(crop_errors={})
            app.select_video()
            app.rotation.set.assert_called_with('Upright — no rotation')
            app.load_preview.assert_called_with(clip, 0)
            app.rotations[clip.path.name] = 180
            app.select_video()
            app.rotation.set.assert_called_with('180°')
            app.load_preview.assert_called_with(clip, 180)


if __name__ == '__main__':
    unittest.main()
