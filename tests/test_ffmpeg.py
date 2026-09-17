"""Small real encode test: pixels, sound, VBO references, idempotence, tamper handling."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import unittest

from goprovbox.media import executable, run, probe
from goprovbox.engine import Scan, export, intersections, fingerprint
from goprovbox.gpmf import TelemetryError
from tests.test_core import fixture, video


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg not installed")
class FFmpegIntegrationTests(unittest.TestCase):
    def test_real_rotated_video_audio_and_safe_rerun(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "GX010001.mp4"
            run([executable("ffmpeg"),"-v","error","-nostdin","-f","lavfi","-i",
                "color=black:s=120x80:r=30,drawbox=x=0:y=0:w=60:h=40:color=red:t=fill,drawbox=x=60:y=0:w=60:h=40:color=green:t=fill,drawbox=x=0:y=40:w=60:h=40:color=blue:t=fill,drawbox=x=60:y=40:w=60:h=40:color=white:t=fill",
                "-f","lavfi","-i","sine=frequency=1000:sample_rate=48000","-t","3","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)])
            vbo = fixture(folder / "VBOX0001.vbo", [f"1200{i//10:02d}.{i%10}00" for i in range(30)])
            clip = video(source,duration=3,rotation=270)
            fingerprints = {p.name:fingerprint(p) for p in (source,vbo.path)}
            scan = Scan(folder,[clip],[vbo],intersections(vbo,clip),[],[],fingerprints)
            result = export(scan,folder / "output",encoder="software")
            report = json.loads((result / "report.json").read_text())
            self.assertEqual(report["status"],"complete")
            movie = result / report["media"][0]["file"]
            meta = probe(movie)
            self.assertTrue(any(s["codec_type"]=="audio" for s in meta["streams"]))
            self.assertEqual(int(meta["streams"][0]["nb_frames"]),90)
            pixels = run([executable("ffmpeg"),"-v","error","-ss","1","-i",str(movie),"-map","0:v:0","-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"])
            offset = (10 * 80 + 10) * 3
            red, green, blue = pixels[offset:offset+3]
            self.assertGreater(green,red+50); self.assertGreater(green,blue+50)
            self.assertEqual(export(scan,result,encoder="software"),result)
            self.assertEqual({p.name:fingerprint(p) for p in (source,vbo.path)},fingerprints)
            output_vbo = result / report["outputs"][0]["file"]
            output_vbo.write_bytes(output_vbo.read_bytes()+b"\n")
            with self.assertRaisesRegex(TelemetryError,"already exists"):
                export(scan,result,encoder="software")


if __name__ == "__main__":
    unittest.main()
