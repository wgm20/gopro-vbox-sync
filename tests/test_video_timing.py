"""Exercise the final MP4 sample, B-frame timing and actual source frame order."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import json
import unittest

from goprovbox.crop import rectangle
from goprovbox.engine import encode, validate_video
from goprovbox.media import executable, probe, run
from tests.test_core import video


class CornerOverlay:
    """A real RGBA pipe, leaving the centre pixel available to identify frames."""
    size = (16, 16)
    position = (0, 0)

    def __init__(self, frames):
        self.frames = frames

    def feed(self, pipe, cancel, errors):
        try:
            with pipe:
                for _ in range(self.frames):
                    if cancel.is_set():
                        return
                    pipe.write(bytes((255, 0, 0, 255)) * 16 * 16)
        except Exception as exc:
            errors.append(exc)


class VideoTimingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def source(self, rate):
        path = self.folder / 'source.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
             f"nullsrc=s=160x160:r={rate},geq=lum='16+mod(N,7)*30':cb=128:cr=128",
             '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
             '-frames:v', '150', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', str(path)])
        return replace(video(path), width=160, height=160, fps=rate,
                       duration=float(150 / Fraction(rate)))

    def pixels(self, path):
        return list(run([executable('ffmpeg'), '-v', 'error', '-i', str(path),
                         '-map', '0:v:0', '-vf', 'crop=2:2:80:40,scale=1:1',
                         '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1']))

    def check_timing(self, source, *, trimmed, overlay, rotated=False):
        fps = Fraction(source.fps)
        first, count = (30, 67) if trimmed else (0, 150)
        clip = replace(source, duration=float(count / fps))
        crop = rectangle(160, 160, 90 if rotated else 0, Fraction(16, 9),
                         {'mode': 'custom', 'x': .5, 'y': .813})
        destination = self.folder / f'output-{trimmed}-{overlay}-{rotated}.mp4'
        encode(clip, destination, 90 if rotated else 0, 160, 'software', lambda _: None,
               lambda _: None, Event(), CornerOverlay(count) if overlay else None, crop,
               float(first / fps) if trimmed else None)
        checked = validate_video(clip, destination, 90 if rotated else 0, 160, crop, count)
        self.assertEqual(checked['frames'], str(count))
        streams = probe(destination)['streams']
        stream = next(s for s in streams if s['codec_type'] == 'video')
        self.assertLess(stream['level'], 30)  # The old lost rate hint produced level 5.2/6.2.
        # MP4's movie edit list rounds to milliseconds; sample timing below must
        # still be exact, including the final frame's full duration.
        self.assertAlmostEqual(float(stream['duration']), float(count / fps), delta=.001)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in streams))
        packets = json.loads(run([executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                                  '-show_packets', '-show_entries', 'packet=pts,dts,duration',
                                  '-of', 'json', str(destination)]))['packets']
        tick = Fraction(stream['time_base'])
        self.assertEqual(sorted(p['pts'] * tick for p in packets), [n / fps for n in range(count)])
        self.assertTrue(all(p.get('duration', 0) * tick == 1 / fps for p in packets))
        self.assertTrue(all(a['dts'] < b['dts'] for a, b in zip(packets, packets[1:])))
        self.assertTrue(any(p['pts'] != p['dts'] for p in packets))  # Includes B frames.
        original, output = self.pixels(source.path), self.pixels(destination)
        self.assertEqual(len(output), count)
        self.assertLess(max(abs(a-b) for a, b in zip(original[first:first+count], output)), 4)

    def test_fractional_rate_custom_crop_overlay_full_and_trimmed(self):
        source = self.source('30000/1001')
        for trimmed in (False, True):
            with self.subTest(trimmed=trimmed):
                self.check_timing(source, trimmed=trimmed, overlay=True)

    def test_integer_rate_custom_crop_overlay(self):
        self.check_timing(self.source('30/1'), trimmed=True, overlay=True)

    def test_high_fractional_rate_rotated_crop_overlay(self):
        self.check_timing(self.source('60000/1001'), trimmed=True, overlay=True, rotated=True)

    def test_custom_crop_without_overlay(self):
        self.check_timing(self.source('30000/1001'), trimmed=True, overlay=False)


if __name__ == '__main__':
    unittest.main()
