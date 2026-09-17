"""Generate original, synthetic practice recordings with real indexed GPMF.

No camera footage, vendor artwork, or recorded location data is used.
The small ISO BMFF metadata track lets the ordinary scan path read this demo.
"""
from datetime import datetime, timezone
from pathlib import Path
import math
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from goprovbox.media import executable, CREATE_NO_WINDOW

DURATION = 12
DAY = (datetime(2026, 1, 1) - datetime(2000, 1, 1)).days


def atom(kind, data):
    return struct.pack(">I4s", 8 + len(data), kind.encode()) + data


def atoms(data):
    offset = 0
    while offset < len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        if size < 8 or offset + size > len(data):
            raise ValueError("Unexpected demo movie atom")
        yield kind.decode(), data[offset:offset + size]
        offset += size


def klv(key, kind, size, data):
    return struct.pack(">4sBBH", key.encode(), ord(kind), size, len(data) // size) + data + b"\0" * (-len(data) % 4)


def container(key, *items):
    return klv(key, "\0", 1, b"".join(items))


def values(seconds):
    theta = seconds * 2 * math.pi / 24
    lat, lon = 51 + .001 * math.sin(theta), .2 + .0015 * math.cos(theta)
    speed = 65 + 20 * math.sin(theta)
    return lat, lon, speed, 4800 + 1000 * math.sin(theta), 50 + 40 * math.sin(theta), max(0, 220 * math.cos(theta))


def packet(second, offset):
    stamp = klv("STMP", "J", 8, struct.pack(">Q", second * 1000000))
    samples = []
    for i in range(10):
        sec = second + offset + i / 10
        lat, lon, speed, *_ = values(sec)
        samples.append(struct.pack(">iiiiiIIHH", round(lat * 1e7), round(lon * 1e7), 100000,
                                   round(speed / 3.6 * 1000), round(speed / 3.6 * 1000),
                                   DAY, 43200000 + round(sec * 1000), 150, 3))
    return container("DEVC",
        container("STRM", stamp, klv("SHUT", "f", 4, struct.pack(">f", 1 / 120))),
        container("STRM", stamp, klv("TYPE", "c", 1, b"lllllLLSS"),
                  klv("SCAL", "l", 4, struct.pack(">9i", 10**7, 10**7, 1000, 1000, 1000, 1, 1000, 100, 1)),
                  klv("GPS9", "?", 32, b"".join(samples))),
        container("STRM", klv("GRAV", "f", 12, struct.pack(">3f", 0, -1, 0) * 10)))


def metadata_track(packets, offset, movie_scale):
    full = b"\0" * 4
    matrix = struct.pack(">9I", 65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824)
    tkhd = atom("tkhd", b"\0\0\0\3" + struct.pack(">5I", 0, 0, 3, 0, DURATION * movie_scale)
                + b"\0" * 16 + matrix + b"\0" * 8)
    mdhd = atom("mdhd", full + struct.pack(">4IHH", 0, 0, 1000, DURATION * 1000, 0x55c4, 0))
    hdlr = atom("hdlr", full + b"\0" * 4 + b"meta" + b"\0" * 12 + b"GoPro MET\0")
    stsd = atom("stsd", full + struct.pack(">I", 1) + atom("gpmd", b"\0" * 6 + b"\0\1"))
    stts = atom("stts", full + struct.pack(">3I", 1, len(packets), 1000))
    stsc = atom("stsc", full + struct.pack(">4I", 1, 1, len(packets), 1))
    stsz = atom("stsz", full + struct.pack(">2I", 0, len(packets)) + b"".join(struct.pack(">I", len(p)) for p in packets))
    stco = atom("stco", full + struct.pack(">2I", 1, offset))
    dinf = atom("dinf", atom("dref", full + struct.pack(">I", 1) + atom("url ", b"\0\0\0\1")))
    minf = atom("minf", atom("nmhd", full) + dinf + atom("stbl", stsd + stts + stsc + stsz + stco))
    return atom("trak", tkhd + atom("mdia", mdhd + hdlr + minf))


def attach_metadata(source, destination, offset):
    parts = list(atoms(source.read_bytes()))
    if parts[-1][0] != "moov":
        raise ValueError("Demo expects non-faststart MP4 with the movie atom last")
    moov = parts[-1][1][8:]
    mvhd = next(value for key, value in atoms(moov) if key == "mvhd")
    scale = struct.unpack_from(">I", mvhd, 20)[0]
    packets = [packet(second, offset) for second in range(DURATION)]
    prefix = b"".join(value for key, value in parts[:-1])
    provisional = atom("moov", moov + metadata_track(packets, 0, scale))
    final = atom("moov", moov + metadata_track(packets, len(prefix) + len(provisional) + 8, scale))
    destination.write_bytes(prefix + final + atom("mdat", b"".join(packets)))


def make_movie(destination, number, offset):
    font = ImageFont.load_default(size=23)
    small = ImageFont.load_default(size=16)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "picture.mp4"
        process = subprocess.Popen([executable("ffmpeg"), "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", "640x360", "-r", "30", "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "fast",
            "-crf", "25", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        try:
            for frame in range(DURATION * 30):
                im = Image.new("RGB", (640, 360), "#a3cfe0"); draw = ImageDraw.Draw(im)
                draw.rectangle((0, 146, 640, 360), fill="#407568")
                draw.polygon([(274,146),(366,146),(570,360),(70,360)], fill="#354555")
                draw.line([(274,146),(70,360)], fill="#f4eacb", width=5)
                draw.line([(366,146),(570,360)], fill="#f4eacb", width=5)
                for stripe in range(7):
                    y = 150 + ((stripe * 38 + frame * 2) % 220)
                    width = 2 + (y - 150) // 24
                    draw.rectangle((320 - width, y, 320 + width, y + 15), fill="#f4eacb")
                draw.rounded_rectangle((18,16,622,80), radius=12, fill="#172d42")
                draw.text((32,24), f"PRACTICE RUN {number}  /  SIMULATED DATA", font=font, fill="white")
                draw.text((32,53), "A safe way to learn. No real racing footage.", font=small, fill="#b8d8d5")
                draw.rounded_rectangle((438,104,622,143), radius=8, fill="#172d42")
                draw.text((449,114), f"UTC 12:00:{offset + frame / 30:04.1f}", font=small, fill="white")
                process.stdin.write(im.tobytes())
        finally:
            process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("Demo video generation failed")
        attach_metadata(path, destination, offset)


def make_vbo(path):
    headers = ["satellites", "time", "latitude", "longitude", "velocity kmh", "heading", "height",
               "vertical velocity m/s", "sampleperiod", "solution type", "avifileindex", "avisynctime", "RPM", "TPS", "Brake"]
    text = "File created on 01/01/2026 @ 12:00:02\n\n[header]\n" + "\n".join(headers)
    text += "\n\n[channel units]\ns\nrpm\n%\npsi\n\n[comments]\nSynthetic practice data. No real vehicle or location recording.\n\n"
    text += "[AVI]\nVBOX0001_\nmp4\n\n[column names]\nsats time lat long velocity heading height vert-vel Tsample solution_type avifileindex avitime RPM TPS Brake\n\n[data]\n"
    for i in range(20, 301):
        sec = i / 10
        lat, lon, speed, rpm, throttle, brake = values(sec)
        text += f"12 1200{sec:06.3f} {lat*60:.7f} {-lon*60:.7f} {speed:.4f} 90 100 0 .1 1 1 {i*100} {rpm:.2f} {throttle:.2f} {brake:.2f}\n"
    path.write_bytes(text.replace("\n", "\r\n").encode("cp1252"))


def main():
    folder = Path(__file__).resolve().parents[1] / "goprovbox/assets/demo"
    folder.mkdir(parents=True, exist_ok=True)
    make_movie(folder / "GH010001.MP4", 1, 0)
    make_movie(folder / "GH010002.MP4", 2, 20)
    make_vbo(folder / "VBOX0001.vbo")
    (folder / "Read me.txt").write_text(
        "PRACTICE RECORDINGS\n\nAll video and telemetry are synthetic, released under MIT.\n"
        "Scan this folder. Both videos are included by default. Try unticking one.\n"
        "Run 1 overlaps 12:00:02 to 12:00:11.9; run 2 overlaps 12:00:20 to 12:00:30.\n"
        "Both are upright. Choose Driving data with Scene blank, then Preview output.\n"
        "Video continues where data is absent; gauges disappear outside the overlap.\n"
        "These files exercise the app, not VBOX authenticity or Circuit Tools acceptance.\n", encoding="utf-8")
    print(folder)


if __name__ == "__main__":
    main()
