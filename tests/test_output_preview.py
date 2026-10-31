"""The main preview shows the crop and overlays that will be exported."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import shutil
import unittest

from PIL import Image, ImageChops, ImageStat

from goprovbox.crop import rectangle
from goprovbox.engine import dimensions, encode, intersections
from goprovbox.framing import FramingPreview, prepare_output_preview
from goprovbox.gpmf import TelemetryError
from goprovbox.media import executable, preview, run
from goprovbox.overlay import Renderer, parse_scene
from tests.test_core import video
from tests.test_overlay import four_channel_vbo, scene_members


class OutputPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.clip = replace(video(self.folder / 'camera.mp4', duration=1), width=20, height=20)
        self.destination = self.folder / 'output.png'

    def decode(self, path, seconds, rotation, destination, *, output_size):
        # Distinct rows reveal the actual source-pixel crop, beyond its shape.
        image = Image.new('RGB', output_size)
        for y in range(image.height):
            for x in range(image.width):
                image.putpixel((x, y), (x * 5, y * 5, 100))
        image.save(destination)

    def prepare(self, *, choice='none', aspect=Fraction(2), mode='none', scene_path='',
                rotation=0, max_size=0, cancel=None):
        return prepare_output_preview(self.clip, [], rotation, max_size, aspect, mode,
                                      scene_path, self.destination, cancel or Event(), choice)

    def test_custom_source_crop_and_opaque_overlay_are_in_saved_rgb_output(self):
        choice = {'mode': 'custom', 'x': .5, 'y': .6}
        scene = object()
        overlay = Image.new('RGBA', (3, 2), (250, 30, 10, 255))
        renderer = SimpleNamespace(position=(2, 1), frame=lambda seconds: overlay)
        with patch('goprovbox.framing.preview', side_effect=self.decode) as decode, \
                patch('goprovbox.overlay.load_scene', return_value=scene) as load, \
                patch('goprovbox.overlay.Renderer', return_value=renderer) as render:
            self.assertEqual(self.prepare(choice=choice, mode='full', scene_path='scene.gvscene'), (20, 10))
        load.assert_called_once_with(Path('scene.gvscene'), mode='full')
        self.assertEqual(render.call_args.args, (self.clip, [], 20, 10, scene))
        self.assertNotEqual(Path(decode.call_args.args[3]), self.destination)
        with Image.open(self.destination) as output:
            self.assertEqual(output.mode, 'RGB')
            self.assertEqual(output.size, (20, 10))
            self.assertEqual(output.getpixel((0, 0)), (0, 30, 100))  # Source row 6.
            self.assertEqual(output.getpixel((2, 1)), (250, 30, 10))
            self.assertEqual(output.getpixel((19, 9)), (95, 75, 100))
        self.assertEqual(list(self.folder.glob('.output-preview-*')), [])

    def test_rotation_then_crop_then_resolution_uses_export_dimensions(self):
        self.clip = replace(self.clip, width=40, height=20)
        renderer = SimpleNamespace(position=(0, 0), frame=lambda seconds: Image.new('RGBA', (1, 1)))
        with patch('goprovbox.framing.preview', side_effect=self.decode) as decode, \
                patch('goprovbox.overlay.Renderer', return_value=renderer) as render:
            size = self.prepare(choice='centre', mode='four', rotation=90, max_size=10)
        self.assertEqual(decode.call_args.args[2], 90)
        self.assertEqual(decode.call_args.kwargs['output_size'], (20, 40))
        self.assertEqual(size, (10, 4))  # Encoder rounds dimensions to even pixels.
        self.assertEqual(render.call_args.args[2:4], size)
        with Image.open(self.destination) as output:
            self.assertEqual(output.size, size)

    def test_no_overlay_ignores_stored_scene_and_builds_only_uncropped_size(self):
        with patch('goprovbox.framing.preview', side_effect=self.decode), \
                patch('goprovbox.overlay.load_scene') as load, \
                patch('goprovbox.overlay.Renderer') as render:
            self.assertEqual(self.prepare(mode='none', scene_path='missing.gvscene'), (20, 20))
        load.assert_not_called(); render.assert_not_called()

    def test_missing_full_scene_fails_before_decoding_without_a_raw_fallback(self):
        for path in ('', None, '  '):
            with self.subTest(path=path), patch('goprovbox.framing.preview') as decode:
                with self.assertRaisesRegex(TelemetryError, 'Choose a VBOX scene'):
                    self.prepare(mode='full', scene_path=path)
                decode.assert_not_called()
                self.assertFalse(self.destination.exists())

    def test_invalid_scene_error_preserves_existing_output(self):
        self.destination.write_bytes(b'existing valid preview')
        with patch('goprovbox.framing.preview', side_effect=self.decode), \
                patch('goprovbox.overlay.load_scene', side_effect=TelemetryError('Invalid scene')), \
                patch('goprovbox.overlay.Renderer') as render:
            with self.assertRaisesRegex(TelemetryError, 'Invalid scene'):
                self.prepare(mode='full', scene_path='invalid.gvscene')
        render.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), b'existing valid preview')

    def test_missing_crop_reference_is_not_silently_replaced_with_no_crop(self):
        with patch('goprovbox.framing.preview') as decode:
            with self.assertRaisesRegex(TelemetryError, 'original VBOX video'):
                self.prepare(choice='centre', aspect=None)
            decode.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_cancellation_before_decode_and_after_composition_does_not_publish(self):
        cancelled = Event(); cancelled.set()
        with patch('goprovbox.framing.preview') as decode:
            with self.assertRaises(InterruptedError):
                self.prepare(cancel=cancelled)
            decode.assert_not_called()
        cancelled.clear()
        prepared = FramingPreview(self.clip, 0, None, Image.new('RGB', (20, 20)),
                                  {False: (20, 20)}, {}, .5)
        def compose(choice, bounds):
            cancelled.set()
            return Image.new('RGBA', bounds, 'red')
        with patch('goprovbox.framing.prepare_preview', return_value=prepared), \
                patch.object(prepared, 'output', side_effect=compose):
            with self.assertRaises(InterruptedError):
                self.prepare(cancel=cancelled)
        self.assertFalse(self.destination.exists())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class EncodedOutputPreviewTests(unittest.TestCase):
    def test_cropped_rotated_overlay_matches_real_encoded_pixels(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / 'camera.mp4'
            run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                 'color=white:s=320x320:r=10,drawbox=x=0:y=0:w=320:h=160:color=red:t=fill',
                 '-t', '1', '-c:v', 'libx264', str(source)])
            clip = replace(video(source, duration=1), width=320, height=320, fps='10/1')
            vbo = four_channel_vbo(folder)
            for row in vbo.rows:
                row.values[vbo.index('brake')] = '100'
            matches = intersections(vbo, clip)
            scene = parse_scene(scene_members())
            choice = {'mode': 'custom', 'x': .5, 'y': .2}
            destination = folder / 'preview.png'
            with patch('goprovbox.overlay.load_scene', return_value=scene):
                size = prepare_output_preview(clip, matches, 90, 160, Fraction(2), 'four',
                                              'synthetic.gvscene', destination, Event(), choice)
            crop = rectangle(320, 320, 90, Fraction(2), choice)
            self.assertEqual(size, dimensions(clip, 90, 160, crop))
            renderer = Renderer(clip, matches, *size, scene)
            encoded = folder / 'encoded.mp4'
            encode(clip, encoded, 90, 160, 'software', lambda message: None,
                   lambda progress: None, Event(), renderer, crop)
            preview(encoded, .5, 0, folder / 'decoded.png', output_size=size)
            with Image.open(destination) as shown, Image.open(folder / 'decoded.png') as actual:
                self.assertEqual(shown.size, (160, 80))
                self.assertLess(max(ImageStat.Stat(ImageChops.difference(
                    shown.convert('RGB'), actual.convert('RGB'))).mean), 10)


if __name__ == '__main__':
    unittest.main()
