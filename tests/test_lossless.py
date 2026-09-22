from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import json
import shutil
import unittest

from goprovbox.engine import Scan, export, intersections, fingerprint, encode
from goprovbox.gpmf import TelemetryError
from goprovbox.lossless import container_rotation, original_reference, validate_original
from goprovbox.media import executable, run, probe, preview
from goprovbox.vbo import read_vbo
from tests.test_core import fixture, video, BASE


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
class LosslessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "GX010493.mp4"
        run([executable("ffmpeg"), "-v", "error", "-nostdin", "-f", "lavfi", "-i",
             "color=black:s=120x80:r=30,drawbox=x=0:y=0:w=60:h=40:color=red:t=fill,drawbox=x=60:y=0:w=60:h=40:color=green:t=fill,drawbox=x=0:y=40:w=60:h=40:color=blue:t=fill,drawbox=x=60:y=40:w=60:h=40:color=white:t=fill",
             "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000", "-t", "3", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.source)])
        self.vbo = fixture(self.folder / "VBOX0001.vbo", [f"1200{i//10:02d}.{i%10}00" for i in range(60)])

    def scan(self, clips):
        return Scan(self.folder, clips, [self.vbo], [m for c in clips for m in intersections(self.vbo, c)], [], [],
                    {p.name: fingerprint(p) for p in [c.path for c in clips] + [self.vbo.path]})

    def stream_hash(self, path):
        return run([executable("ffmpeg"), "-v", "error", "-nostdin", "-noautorotate", "-i", str(path),
                    "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"])

    def test_removed_original_mode_cannot_create_an_export(self):
        scan = self.scan([video(self.source, duration=3)])
        with self.assertRaisesRegex(TelemetryError, "has been removed"):
            export(scan, self.folder / "output", video_mode="original")
        self.assertFalse((self.folder / "output").exists())

    def test_rotation_copies_exact_video_and_audio_streams_and_displays_upright(self):
        before = fingerprint(self.source)
        original_hash = self.stream_hash(self.source)
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                movie = self.folder / f"diagnostic{rotation}.mp4"
                encode(video(self.source,duration=3),movie,rotation,0,"copy",lambda m:None,lambda v:None,Event())
                self.assertEqual(container_rotation(probe(movie)), rotation)
                self.assertEqual(self.stream_hash(movie), original_hash)
                self.assertEqual(fingerprint(self.source), before)
                if rotation == 270:
                    pixels = run([executable("ffmpeg"), "-v", "error", "-ss", "1", "-i", str(movie),
                                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
                    r, g, b = pixels[(10*80+10)*3:(10*80+10)*3+3]
                    self.assertGreater(g, r+50); self.assertGreater(g, b+50)

    def test_existing_correct_rotation_is_reused_and_override_replaces_it_once(self):
        rotated = self.folder / "GX010494.mp4"
        run([executable("ffmpeg"), "-v", "error", "-display_rotation", "90", "-noautorotate", "-i", str(self.source),
             "-map", "0:v:0", "-map", "0:a?", "-c", "copy", str(rotated)])
        clip = video(rotated, duration=3, rotation=270)
        self.assertIsNotNone(original_reference(clip,self.folder/"unused",270,probe(rotated)))
        overridden=self.folder/"diagnostic.mp4"
        encode(clip,overridden,0,0,"copy",lambda m:None,lambda v:None,Event())
        self.assertEqual(container_rotation(probe(overridden)),0)
        self.assertEqual(self.stream_hash(overridden),self.stream_hash(rotated))

    def test_chapter_indices_preserve_a_single_continuous_vbo_after_encoding(self):
        second = self.folder / "GX020493.mp4"
        shutil.copyfile(self.source, second)
        clips = [video(self.source, duration=3), video(second, start=BASE+3, duration=3)]
        result = export(self.scan(clips), self.folder / "chapters", encoder="software", overlap_only=False)
        report = json.loads((result / "report.json").read_text())
        self.assertEqual(len(report["outputs"]), 1)
        self.assertEqual(len(report["outputs"][0]["videos"]), 2)
        exported = read_vbo(result / report["outputs"][0]["file"])
        self.assertEqual(len(exported.rows), 60)
        self.assertEqual({int(r.values[exported.index("avifileindex")]) for r in exported.rows}, {1,2})
        for movie in result.glob("*.mp4"):
            self.assertEqual(probe(movie)["streams"][0]["nb_frames"],"90")

    def test_nonstandard_filename_is_encoded_and_cancel_does_not_publish(self):
        renamed = self.folder / "my-session.mp4"
        shutil.copyfile(self.source, renamed)
        scan = self.scan([video(renamed, duration=3)])
        result = export(scan, self.folder / "renamed", encoder="software")
        self.assertEqual(probe(next(result.glob("*.mp4")))["streams"][0]["codec_name"],"h264")
        cancel = Event(); cancel.set()
        with self.assertRaises(InterruptedError):
            export(scan, self.folder / "cancelled", encoder="software", cancel=cancel)
        self.assertFalse((self.folder / "cancelled").exists())

    def test_validation_rejects_missing_audio_and_wrong_rotation(self):
        muted = self.folder / "muted.mp4"
        run([executable("ffmpeg"), "-v", "error", "-i", str(self.source), "-map", "0:v:0", "-c", "copy", str(muted)])
        with self.assertRaisesRegex(TelemetryError, "audio track"):
            validate_original(video(self.source, duration=3), muted, 0, probe(self.source))
        with self.assertRaisesRegex(TelemetryError, "rotation"):
            validate_original(video(self.source, duration=3), self.source, 270, probe(self.source))

    def test_preview_recovers_when_its_cache_folder_was_removed(self):
        cache = self.folder / "preview-cache"
        cache.mkdir(); cache.rmdir()
        destination = cache / "frame.png"
        preview(self.source, 1, 270, destination, 80, max_height=120)
        meta = probe(destination)["streams"][0]
        self.assertEqual((meta["width"], meta["height"]), (80, 120))


if __name__ == "__main__":
    unittest.main()
