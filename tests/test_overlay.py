from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import io
import json
import shutil
import tarfile
import unittest

from PIL import Image
from goprovbox.engine import Match, Scan, export, fingerprint, encode, dimensions
from goprovbox.media import run, executable, probe
from goprovbox.overlay import parse_scene, archive_members, Renderer, Timeline
from goprovbox.gpmf import TelemetryError
from goprovbox.vbo import Row
from tests.test_core import fixture, video, BASE


def png(size, colour):
    buf = io.BytesIO(); Image.new("RGBA", size, colour).save(buf, format="PNG"); return buf.getvalue()


def scene_members():
    return {"SCENE.XML": b'''<RLCFG><scene><images><image name="bar" path="bar.png"/></images>
      <fonts><font name="digits" glyphs="font.png">(0x30,0),(0x31,5),(0x32,10),(0x33,15),(0x34,20),(0x35,25),(0x36,30),(0x37,35),(0x38,40),(0x39,45),(0x2d,50)</font></fonts>
      <graphics>
        <text name="speed" datasource="vbox.speed_gnd_mps scaling=2.236936292054402" offset="(0,0,30,12)" font="digits" fmtstr="%03lu"/>
        <text name="rpm" datasource="can.bin[2].channel(RPM)" offset="(0,0,30,12)" font="digits" fmtstr="%04ld"/>
        <multibar name="throttle" datasource="can.bin[2].channel(TPS)" background="bar"><graphics_bar size="(10,40)" offset="(0,0)" colour="(255,0,255,0)" range="(0,100)" barmode="bottom_top_min"/></multibar>
        <multibar name="brake" datasource="can.bin[4].channel(Brake)" background="bar"><graphics_bar size="(10,40)" offset="(0,0)" colour="(255,255,0,0)" range="(0,600)" barmode="bottom_top_min"/></multibar>
        <text name="laps" datasource="laptiming.lap_time_ms"/>
        <static_image name="pipborder" background="bar"/>
      </graphics><video_mappings><video_mapping name="Cam2"/></video_mappings>
      <layouts><layout format="(320,180)"><graphic name="speed" screen_pos="(200,0)"/><graphic name="rpm" screen_pos="(230,0)"/>
      <graphic name="throttle" screen_pos="(270,0)"/><graphic name="brake" screen_pos="(290,0)"/>
      <graphic name="laps" screen_pos="(10,10)"/><graphic name="pipborder" screen_pos="(0,0)"/></layout></layouts>
      </scene></RLCFG>''',
      "VBOXHD.XML": b'<RLCFG><channels><channel name="RPM" units="rpm"/><channel name="TPS" units="%"/><channel name="Brake" units="psi"/></channels></RLCFG>',
      "bar.png": png((10,40),(0,0,0,0)), "font.png": png((55,12),(255,255,255,255))}


def four_channel_vbo(folder):
    vbo = fixture(folder / "test.vbo", [f"12000{i//10}.{i%10}00" for i in range(31)])
    vbo.columns.append("brake")
    # Add the matching header/unit in the otherwise authentic VBO fixture.
    vbo.preamble.insert(vbo.preamble.index("[channel units]")-1, "Brake")
    vbo.preamble.insert(vbo.preamble.index("[comments]")-1, "psi")
    vbo.preamble[vbo.preamble.index("[column names]")+1] += " Brake"
    for i, row in enumerate(vbo.rows): row.values.append(str(min(600, i*30)))
    return vbo


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.folder=Path(self.temp.name)
        self.vbo=four_channel_vbo(self.folder); self.clip=replace(video(self.folder/"video.mp4",duration=3),width=320,height=180)
        self.scene=parse_scene(scene_members())
        self.match=Match(self.vbo,self.clip,self.vbo.rows[10:21],0,0)

    def test_calibration_units_and_interpolation(self):
        timeline=Timeline([self.match],self.scene.bindings)
        values=timeline.at(BASE+1.05)
        self.assertAlmostEqual(values["speed"],36/1.609344,places=8)
        self.assertEqual(values["rpm"],5500)
        self.assertEqual(values["throttle"],42)  # no second application of CAN scaling
        self.assertAlmostEqual(values["brake"],315,places=3)
        self.assertIsNone(timeline.at(BASE+.999)); self.assertIsNone(timeline.at(BASE+2.001))

    def test_gaps_and_ambiguous_sources(self):
        match=replace(self.match,rows=[self.vbo.rows[0],self.vbo.rows[10]])
        self.assertIsNone(Timeline([match],self.scene.bindings).at(BASE+.5))
        with self.assertRaisesRegex(TelemetryError,"ambiguous"):
            Timeline([self.match,self.match],self.scene.bindings)

    def test_missing_channel_and_wrong_unit(self):
        with self.assertRaisesRegex(TelemetryError,"required channel missing"):
            Timeline([replace(self.match,vbo=fixture(self.folder/"missing.vbo"))],self.scene.bindings)
        self.vbo.preamble[self.vbo.preamble.index("psi")]="bar"
        with self.assertRaisesRegex(TelemetryError,"unit is bar"):
            Timeline([self.match],self.scene.bindings)

    def test_scene_excludes_pip_and_unavailable_widgets(self):
        self.assertIn("Cam2",self.scene.omitted);self.assertIn("pipborder",self.scene.omitted);self.assertIn("laps",self.scene.omitted)
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        self.assertIsNone(renderer.frame(.5).getbbox())
        self.assertIsNotNone(renderer.frame(1.5).getbbox())
        self.assertIsNone(renderer.frame(2.5).getbbox())
        # Brake at 450 psi fills 75% of a 600 psi bar.
        im=renderer.frame(1.5)
        self.assertEqual(im.getpixel((95,20)),(255,0,0,255))
        self.assertEqual(im.getpixel((95,5))[3],0)

    def test_invalid_scenes_do_not_silently_guess(self):
        members=scene_members(); members["SCENE.XML"]=members["SCENE.XML"].replace(b'<text name="rpm"',b'<gauge name="rpm"')
        with self.assertRaisesRegex(TelemetryError,"gauge widget"):
            parse_scene(members)
        members=scene_members(); members["SCENE.XML"]=b'<!DOCTYPE bad>'+members["SCENE.XML"]
        with self.assertRaisesRegex(TelemetryError,"declarations"):
            parse_scene(members)

    def test_archive_rejects_traversal_links_duplicates_and_limits(self):
        for name, link in [("../SCENE.XML",False),("C:/bad",False),("link",True)]:
            buf=io.BytesIO()
            with tarfile.open(fileobj=buf,mode="w") as t:
                member=tarfile.TarInfo(name);member.size=1
                if link:member.type=tarfile.SYMTYPE;member.linkname="elsewhere"
                t.addfile(member,io.BytesIO(b"a"))
            with self.assertRaisesRegex(TelemetryError,"Unsafe"):
                archive_members(buf.getvalue())
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode="w") as t:
            for _ in range(2):
                member=tarfile.TarInfo("same");member.size=1;t.addfile(member,io.BytesIO(b"a"))
        with self.assertRaisesRegex(TelemetryError,"duplicate"):
            archive_members(buf.getvalue())
        with patch("goprovbox.overlay.MAX_ARCHIVE",0):
            with self.assertRaisesRegex(TelemetryError,"size limits"):
                archive_members(buf.getvalue())

    def test_drift_clock_is_used_and_builtin_layout_fits_portrait(self):
        clip=replace(self.clip,clock=replace(self.clip.clock,rate=1.001))
        renderer=Renderer(clip,[self.match],180,320)
        self.assertLessEqual(renderer.position[0]+renderer.size[0],180)
        self.assertLessEqual(renderer.position[1]+renderer.size[1],320)
        self.assertAlmostEqual(renderer.timeline.at(clip.clock.utc(1.5))["brake"],450.45,places=3)

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
    def test_cancellation_stops_overlay_producer_without_publishing(self):
        run([executable("ffmpeg"),"-v","error","-f","lavfi","-i","color=black:s=320x180:r=30","-t","3","-c:v","libx264",str(self.clip.path)])
        cancel=Event();renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        frame=renderer.frame
        def cancel_at_first_frame(seconds):
            cancel.set();return frame(seconds)
        with patch.object(renderer,"frame",side_effect=cancel_at_first_frame):
            with self.assertRaises(InterruptedError):
                encode(self.clip,self.folder/"cancelled.mp4",0,0,"software",lambda m:None,lambda p:None,cancel,renderer)

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
    def test_renderer_failure_is_not_reported_as_a_success(self):
        run([executable("ffmpeg"),"-v","error","-f","lavfi","-i","color=black:s=320x180:r=30","-t","3","-c:v","libx264",str(self.clip.path)])
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        frame=renderer.frame
        def broken(seconds):
            if seconds>.5:raise ValueError("drawing failed")
            return frame(seconds)
        with patch.object(renderer,"frame",side_effect=broken):
            with self.assertRaisesRegex(TelemetryError,"drawing failed"):
                encode(self.clip,self.folder/"failed.mp4",0,0,"software",lambda m:None,lambda p:None,Event(),renderer)

    def test_original_mode_refuses_burn_in_before_creating_files(self):
        scan=Scan(self.folder,[self.clip],[self.vbo],[self.match],[],[],{})
        with self.assertRaisesRegex(TelemetryError,"requires re-encoding"):
            export(scan,self.folder/"out",telemetry_overlay=True,video_mode="original")
        self.assertFalse((self.folder/"out").exists())

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
    def test_real_encode_keeps_audio_and_timing_and_hides_outside_overlap(self):
        run([executable("ffmpeg"),"-v","error","-f","lavfi","-i","color=black:s=320x180:r=30", "-f","lavfi","-i","sine=frequency=500", "-t","3","-c:v","libx264","-c:a","aac",str(self.clip.path)])
        dest=self.folder/"overlay.mp4"
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        encode(self.clip,dest,0,0,"software",lambda m:None,lambda p:None,Event(),renderer)
        meta=probe(dest);stream=meta["streams"][0]
        self.assertEqual(stream["nb_frames"],"90");self.assertEqual(stream["avg_frame_rate"],"30/1")
        self.assertTrue(any(s["codec_type"]=="audio" for s in meta["streams"]))
        for time,present in [(.5,False),(1.5,True),(2.5,False)]:
            raw=run([executable("ffmpeg"),"-v","error","-ss",str(time),"-i",str(dest),"-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"])
            offset=(20*320+295)*3;r,g,b=raw[offset:offset+3]
            if present:self.assertGreater(r,150);self.assertLess(g,80)
            else:self.assertLess(r,20)

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
    def test_export_report_preserves_data_with_scene_and_reuses_completed_output(self):
        # Save the extended fixture through the product writer to exercise export verification.
        from goprovbox.vbo import write_vbo,read_vbo
        write_vbo(self.vbo,self.vbo.rows,list(range(len(self.vbo.rows))),"test_",self.vbo.path)
        self.vbo=read_vbo(self.vbo.path)
        run([executable("ffmpeg"),"-v","error","-f","lavfi","-i","color=black:s=320x180:r=30","-t","3","-c:v","libx264",str(self.clip.path)])
        match=replace(self.match,vbo=self.vbo,rows=self.vbo.rows[10:21])
        scan=Scan(self.folder,[self.clip],[self.vbo],[match],[],[],{p.name:fingerprint(p) for p in (self.clip.path,self.vbo.path)})
        with patch("goprovbox.overlay.load_scene",return_value=self.scene):
            result=export(scan,self.folder/"out",overlay_scene=self.folder/"scene.vvhsn",encoder="software")
            self.assertEqual(export(scan,result,overlay_scene=self.folder/"scene.vvhsn",encoder="software"),result)
        report=json.loads((result/"report.json").read_text())
        self.assertTrue(report["outputs"][0]["telemetry_preserved"])
        self.assertIn("Cam2",report["settings"]["overlay"]["omitted_elements"])


if __name__=="__main__":unittest.main()
