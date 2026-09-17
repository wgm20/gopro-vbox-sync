from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import json
import math
import os
import struct
import unittest

from goprovbox.gpmf import TelemetryError, Record, records, streams, scaled
from goprovbox.media import Clock, GPSPoint, Video, Orientation, fit_clock, detect_orientation, inspect_video
from goprovbox.vbo import read_vbo, write_vbo, time_of_day
from goprovbox.engine import (intersections, check_pair, recording_groups, Scan, Match, fingerprint,
                             export, verify_vbo, dimensions)

UTC = timezone.utc
BASE = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()


def klv(key, kind, size, data, count=None):
    if count is None:
        count = len(data) // size
    return struct.pack(">4sBBH", key.encode(), ord(kind), size, count) + data + b"\0" * (-len(data) % 4)


def container(key, children):
    data = b"".join(children)
    return klv(key, "\0", 1, data)


def fixture(path, times=None, date="10/07/2026", duplicate=False, gap=False):
    times = times or [f"1200{i // 10:02d}.{i % 10}00" for i in range(101)]
    rows = []
    for i, t in enumerate(times):
        rows.append(f"012 {t} +3060.0000 -0012.0000 036.000 090.000 +00100.00 0.000 0.100 01 0001 {i*100:09d} +5.500000E+03 +4.200000E+01")
        if duplicate and i == 2:
            rows.append(rows[-1])
    text = f"""File created on {date} @ 12:00:00

[header]
satellites
time
latitude
longitude
velocity kmh
heading
height
vertical velocity m/s
sampleperiod
solution type
avifileindex
avisynctime
RPM
TPS

[channel units]
s
rpm
%

[comments]
Original recorder comment

[AVI]
VBOX0001_
mp4

[laptiming]
Start -12.0 +3060.0 -12.1 +3060.1 ¬ Start / Finish

[column names]
sats time lat long velocity heading height vert-vel Tsample solution_type avifileindex avitime RPM TPS

[data]
""" + "\n".join(rows) + "\n"
    path.write_bytes(text.replace("\n", "\r\n").encode("cp1252"))
    return read_vbo(path)


def video(path, start=BASE, duration=10, rotation=0):
    gps = [GPSPoint(i / 10, start + i / 10, 51, .2, 36) for i in range(int(duration * 10))]
    return Video(path, duration, 120, 80, "30/1", "h264", 0,
                 Clock(start, 1, 10, 0, 1, 0, duration, "test"),
                 Orientation(rotation, 1, "test"), gps)


class GPMFTests(unittest.TestCase):
    def test_nested_big_endian_and_padding(self):
        payload = container("DEVC", [container("STRM", [klv("STNM", "c", 1, b"GPS"), klv("SCAL", "l", 4, struct.pack(">i", 100))])])
        data = list(streams(payload))[0]
        self.assertEqual(data[0].text(), "GPS")
        self.assertEqual(data[1].values(), [(100,)])

    def test_mixed_gps9_types_and_scales(self):
        types = "lllllLLSS"
        raw = struct.pack(">iiiiiIIHH", 513000000, -2000000, 120000, 12345, 12400, 9687, 43200100, 175, 3)
        item = Record("GPS9", "?", 32, 1, raw)
        siblings = {"TYPE": Record("TYPE", "c", 1, 9, types.encode()),
                    "SCAL": Record("SCAL", "l", 4, 9, struct.pack(">9i", 10**7,10**7,1000,1000,1000,1,1000,100,1))}
        value = scaled(item, siblings)[0]
        self.assertAlmostEqual(value[0], 51.3); self.assertAlmostEqual(value[1], -.2)
        self.assertEqual(value[5:], (9687, 43200.1, 1.75, 3))

    def test_complex_array_descriptor(self):
        item = Record("TEST", "?", 12, 1, struct.pack(">3i", -1, 2, 3))
        self.assertEqual(item.values("l[3]"), [(-1, 2, 3)])

    def test_truncated_header(self):
        with self.assertRaises(TelemetryError):
            records(b"GPS9?")

    def test_truncated_payload(self):
        with self.assertRaises(TelemetryError):
            records(struct.pack(">4sBBH", b"GPS9", ord("l"), 4, 20) + b"1234")

    def test_excessive_nesting(self):
        data = klv("TEST", "c", 1, b"x")
        for _ in range(18):
            data = container("DEVC", [data])
        with self.assertRaises(TelemetryError):
            records(data)

    def test_zero_scale(self):
        item = Record("TEST", "l", 4, 1, struct.pack(">i", 20))
        with self.assertRaises(TelemetryError):
            scaled(item, {"SCAL": Record("SCAL", "l", 4, 1, struct.pack(">i", 0))})

    def test_invalid_key_and_padding(self):
        for data in (b"\x01xxx" + b"c\x01\x00\x01" + b"xxxx", b"\0" * 8 + b"GPS9"):
            with self.subTest(data=data), self.assertRaises(TelemetryError):
                records(data)


class ClockTests(unittest.TestCase):
    def test_legacy_gps5_fallback_and_quality_filter(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "GH010001.mp4"
            packets, data = [], b""
            for i in range(20):
                gps = container("STRM", [klv("GPSU", "U", 16, f"2607101200{i:02d}.000".encode()),
                      klv("GPSF", "L", 4, struct.pack(">I", 0 if i < 2 else 3)),
                      klv("GPSP", "S", 2, struct.pack(">H", 150)),
                      klv("SCAL", "l", 4, struct.pack(">5i",10**7,10**7,1000,1000,1000)),
                      klv("GPS5", "l", 20, struct.pack(">5i",510000000,2000000,100000,10000,10000) * 10)])
                payload = container("DEVC", [gps])
                packets.append({"pos":str(len(data)),"size":str(len(payload)),"pts_time":str(i),"duration_time":"1"})
                data += payload
            path.write_bytes(data)
            meta = {"streams":[{"codec_type":"video","width":120,"height":80,"avg_frame_rate":"30/1","codec_name":"h264","duration":"20"},{"codec_tag_string":"gpmd","index":3}],"format":{"duration":"20"}}
            with patch("goprovbox.media.executable",return_value="ffprobe"), patch("goprovbox.media.run",return_value=json.dumps({"packets":packets}).encode()):
                result = inspect_video(path,meta)
            self.assertAlmostEqual(result.clock.origin,BASE,places=5)
            self.assertEqual(result.clock.anchors,18)
            self.assertEqual(result.clock.first_fix,2)
            self.assertIn("lower precision",result.clock.method)
            self.assertTrue(result.warnings)

    def test_affine_drift_and_outlier_rejection(self):
        points = [GPSPoint(i, BASE + i * 1.00004 + math.sin(i) * .001, 0,0,0) for i in range(600)]
        points[30].utc += 5
        clock = fit_clock(points, "test")
        self.assertAlmostEqual(clock.origin, BASE, places=3)
        self.assertAlmostEqual(clock.rate, 1.00004, places=7)
        self.assertEqual(clock.rejected, 1)
        self.assertLess(clock.residual_p95_ms, 2)
        self.assertAlmostEqual(clock.media_time(clock.utc(300)), 300, places=5)

    def test_insufficient_gps(self):
        with self.assertRaises(TelemetryError):
            fit_clock([GPSPoint(0, BASE,0,0,0)], "test")

    def test_clock_jump_rejected(self):
        points = [GPSPoint(i, BASE + i + (10 if i >= 50 else 0),0,0,0) for i in range(100)]
        with self.assertRaises(TelemetryError):
            fit_clock(points, "test")

    def test_time_lapse_rejected(self):
        points = [GPSPoint(i, BASE + i * 2,0,0,0) for i in range(100)]
        with self.assertRaises(TelemetryError):
            fit_clock(points, "test")

    def test_gps9_packet_extract_uses_video_clock_and_ignores_bad_fix(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "GX010001.mp4"
            packets, data = [], b""
            # SHUT origin is 10 seconds in the hardware clock. GPS samples
            # arrive 250 ms after each video's packet beginning.
            for i in range(25):
                shutter = container("STRM", [klv("STMP", "J", 8, struct.pack(">Q", 10_000_000 + i*1_000_000)), klv("SHUT", "f", 4, struct.pack(">f", .01))])
                vals = struct.pack(">9i", 510000000, 2000000, 100000, 10000, 10000, 9687, 43200250 + i*1000, 150, 0 if i < 5 else 3)
                gps = container("STRM", [klv("STMP", "J", 8, struct.pack(">Q",10_250_000 + i*1_000_000)),
                      klv("SCAL", "l", 4, struct.pack(">9i",10**7,10**7,1000,1000,1000,1,1000,100,1)), klv("GPS9", "l", 36, vals)])
                payload = container("DEVC", [shutter, gps])
                packets.append({"pos": str(len(data)), "size": str(len(payload)), "pts_time": str(i), "duration_time": "1"})
                data += payload
            path.write_bytes(data)
            meta = {"streams": [{"codec_type":"video","width":120,"height":80,"avg_frame_rate":"30/1","codec_name":"h264","duration":"25"}, {"codec_tag_string":"gpmd","index":3}], "format":{"duration":"25"}}
            with patch("goprovbox.media.executable", return_value="ffprobe"), patch("goprovbox.media.run", return_value=json.dumps({"packets":packets}).encode()):
                result = inspect_video(path, meta)
            self.assertAlmostEqual(result.clock.origin, BASE, places=5)
            self.assertEqual(result.clock.anchors, 20)
            self.assertAlmostEqual(result.clock.first_fix, 5.25)


class OrientationTests(unittest.TestCase):
    def test_all_four_orientations(self):
        for vector, expected in [((0,-1,0),0), ((1,0,0),90), ((0,1,0),180), ((-1,0,0),270)]:
            with self.subTest(expected=expected):
                self.assertEqual(detect_orientation([vector] * 100, []).clockwise, expected)

    def test_sideways_actual_camera_pattern(self):
        result = detect_orientation([(-.91,-.06,.4)] * 100, [(1,0,0,0)] * 100)
        self.assertEqual(result.clockwise,270)
        self.assertGreater(result.confidence,.99)

    def test_conflicting_orientation_needs_review(self):
        result = detect_orientation([(0,-1,0)] * 50 + [(0,1,0)] * 50, [])
        self.assertIsNone(result.clockwise)

    def test_missing_gravity_and_large_image_transform(self):
        self.assertIsNone(detect_orientation([], []).clockwise)
        self.assertIsNone(detect_orientation([(0,-1,0)] * 100, [(0,0,0,1)] * 100).clockwise)

    def test_container_rotation_is_baked_once(self):
        self.assertEqual(detect_orientation([], [], 90).clockwise, 270)
        self.assertIsNone(detect_orientation([], [], 45).clockwise)


class VBOTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.folder = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()

    def test_midnight_rollover(self):
        vbo = fixture(self.folder / "night.vbo", ["235959.900", "000000.000", "000000.100"])
        self.assertAlmostEqual(vbo.rows[2].utc - vbo.rows[0].utc, .2, places=5)
        self.assertEqual(datetime.fromtimestamp(vbo.rows[2].utc,UTC).day, 11)

    def test_exact_duplicates_removed(self):
        vbo = fixture(self.folder / "data.vbo", duplicate=True)
        self.assertEqual(len(vbo.rows),101)
        self.assertIn("Removed 1 exact",vbo.warnings[0])

    def test_conflicting_duplicates_rejected(self):
        with self.assertRaisesRegex(TelemetryError,"conflicting"):
            fixture(self.folder / "data.vbo", ["120000.000","120000.000","120000.100"])

    def test_time_goes_backwards_rejected(self):
        with self.assertRaisesRegex(TelemetryError,"backwards"):
            fixture(self.folder / "data.vbo", ["120001.000","120000.000"])

    def test_bad_numeric_and_channel_count_rejected(self):
        path = self.folder / "data.vbo"
        fixture(path)
        data = path.read_bytes()
        for old, new in [(b"+5.500000E+03",b"nan"), (b"avitime RPM TPS",b"avitime RPM")]:
            path.write_bytes(data.replace(old,new))
            with self.subTest(new=new), self.assertRaises(TelemetryError):
                read_vbo(path)

    def test_invalid_time(self):
        for value in ("240000.00", "126100.00", "125960.00", "12:00:00", "nan"):
            with self.subTest(value=value), self.assertRaises(TelemetryError):
                time_of_day(value)

    def test_lossless_export_and_chapter_indices(self):
        source = fixture(self.folder / "input.vbo")
        before = source.path.read_bytes()
        rows = source.rows[10:30]
        times = [i/10 for i in range(10)] * 2
        indices = [1] * 10 + [2] * 10
        target = self.folder / "GoPro_test.vbo"
        write_vbo(source, rows, times,"GoPro_test_",target,indices)
        videos = [video(self.folder / "v1.mp4"),video(self.folder / "v2.mp4")]
        verify_vbo(target,source,rows,times,indices,videos)
        self.assertEqual(source.path.read_bytes(),before)
        result = target.read_bytes()
        self.assertIn(b"GoPro_test_\r\nmp4",result)
        self.assertIn(b"Original recorder comment",result)
        self.assertIn(b"\xac Start / Finish",result)

    def test_partial_overlap_bounds(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "clip.mp4",BASE+2,5)
        match = intersections(vbo,clip)[0]
        self.assertEqual(match.rows[0].utc,BASE+2)
        self.assertLess(match.rows[-1].utc,BASE+7)
        self.assertEqual(len(match.rows),50)
        self.assertIsNone(match.position_median_m)  # Four interior GPS checks: insufficient evidence.
        full = intersections(vbo, video(self.folder / "full.mp4"))[0]
        self.assertAlmostEqual(full.position_median_m, 0)

    def test_no_overlap_and_wrong_day(self):
        vbo = fixture(self.folder / "data.vbo")
        self.assertEqual(intersections(vbo,video(self.folder / "clip.mp4",BASE+86400)),[])

    def test_gap_is_split(self):
        times = [f"1200{i//10:02d}.{i%10}00" for i in range(30)] + [f"1200{i//10:02d}.{i%10}00" for i in range(60,100)]
        vbo = fixture(self.folder / "gap.vbo",times)
        self.assertEqual(len(intersections(vbo,video(self.folder / "clip.mp4"))),2)

    def test_wrong_location_is_rejected(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "wrong.mp4")
        for point in clip.gps:
            point.lat = 52
        with self.assertRaisesRegex(TelemetryError,"tracks disagree"):
            intersections(vbo,clip)

    def test_chapters_join_only_for_same_recording_and_continuous_utc(self):
        clips = [video(Path("GX010493.mp4")), video(Path("GX020493.mp4"),BASE+10),
                 video(Path("GX030493.mp4"),BASE+30), video(Path("GX010494.mp4"),BASE+40)]
        self.assertEqual([len(g) for g in recording_groups(clips)],[2,1,1])

    def test_portrait_size_respects_aspect_and_no_upscale(self):
        clip = video(Path("video.mp4"))
        self.assertEqual(dimensions(clip,270,1920),(80,120))
        self.assertEqual(dimensions(clip,0,60),(60,40))

    def test_unsafe_output_refused(self):
        scan = Scan(self.folder,[],[],[],[],[],{})
        for output in (self.folder,self.folder.parent):
            with self.subTest(output=output), self.assertRaisesRegex(TelemetryError,"separate output"):
                export(scan,output)

    def test_unsafe_output_refused_with_noncanonical_source_path(self):
        scan = Scan(Path(os.path.relpath(self.folder)), [], [], [], [], [], {})
        for output in (self.folder, self.folder.parent):
            with self.subTest(output=output), self.assertRaisesRegex(TelemetryError, "separate output"):
                export(scan, output)

    def test_unknown_rotation_requires_review(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "video.mp4", rotation=None)
        match = intersections(vbo,clip)
        scan = Scan(self.folder,[clip],[vbo],match,[],[],{})
        with self.assertRaisesRegex(TelemetryError,"choose a rotation"):
            export(scan,self.folder / "output")

    def test_misspelled_rotation_override_is_rejected(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "video.mp4")
        scan = Scan(self.folder,[clip],[vbo],intersections(vbo,clip),[],[],{})
        with self.assertRaisesRegex(TelemetryError,"unknown GoPro"):
            export(scan,self.folder / "output",rotations={"typo.mp4":90})

    def test_source_change_after_scan_refused(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "clip.mp4")
        scan = Scan(self.folder,[clip],[vbo],intersections(vbo,clip),[],[],{vbo.path.name:fingerprint(vbo.path)})
        vbo.path.write_bytes(vbo.path.read_bytes() + b"\n")
        with self.assertRaisesRegex(TelemetryError,"changed since scan"):
            export(scan,self.folder / "output")

    def test_failure_never_publishes_output(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "clip.mp4")
        scan = Scan(self.folder,[clip],[vbo],intersections(vbo,clip),[],[],{})
        with patch("goprovbox.engine.encode", side_effect=TelemetryError("test failure")):
            with self.assertRaises(TelemetryError):
                export(scan,self.folder / "output")
        self.assertFalse((self.folder / "output").exists())
        reports = list(self.folder.glob(".output.working-*/report.json"))
        self.assertEqual(json.loads(reports[0].read_text())["status"],"failed")

    def test_cancel_before_encoding_never_publishes(self):
        vbo = fixture(self.folder / "data.vbo")
        clip = video(self.folder / "clip.mp4")
        scan = Scan(self.folder,[clip],[vbo],intersections(vbo,clip),[],[],{})
        cancel = Event(); cancel.set()
        with self.assertRaises(InterruptedError):
            export(scan,self.folder / "output",cancel=cancel)
        self.assertFalse((self.folder / "output").exists())


if __name__ == "__main__":
    unittest.main()
