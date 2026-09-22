"""Media inspection, GPS clock estimation, and orientation evidence."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from collections import Counter
import json
import math
import os
import shutil
import struct
import subprocess

from .gpmf import TelemetryError, records, streams, scaled

UTC = timezone.utc
EPOCH2000 = datetime(2000, 1, 1, tzinfo=UTC).timestamp()
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def executable(name: str) -> str:
    from .distribution import tools_directory
    override = os.environ.get("GOPROVBOX_" + name.upper())
    candidates = [override, str(tools_directory() / (name + ".exe")), shutil.which(name)]
    if os.name == "nt":
        candidates += [str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) /
                           "Racelogic/Circuit Tools 3/Miscellaneous" / (name + ".exe"))]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise TelemetryError(f"{name} is required. Choose Help > Set up video tools, "
                         "or install FFmpeg and add its bin folder to PATH, "
                         f"or set GOPROVBOX_{name.upper()} to the executable.")


def run(args: list[str], *, timeout: float = 180) -> bytes:
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise TelemetryError(f"{Path(args[0]).name} timed out after {timeout:g} seconds") from exc
    if result.returncode:
        raise TelemetryError(result.stderr.decode("utf-8", "replace")[-3000:].strip() or
                             f"{Path(args[0]).name} exited with code {result.returncode}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run([executable("ffprobe"), "-v", "error", "-show_streams",
                           "-show_format", "-of", "json", str(path)]))


def utc_text(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class GPSPoint:
    media_time: float
    utc: float
    lat: float
    lon: float
    speed: float  # km/h


@dataclass
class Clock:
    origin: float
    rate: float
    anchors: int
    rejected: int
    residual_p95_ms: float
    first_fix: float
    last_fix: float
    method: str

    def utc(self, media_time: float) -> float:
        return self.origin + self.rate * media_time

    def media_time(self, utc: float) -> float:
        return (utc - self.origin) / self.rate


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    return values[min(len(values) - 1, int((len(values) - 1) * p))]


def fit_clock(points: list[GPSPoint], method: str) -> Clock:
    """Robust affine fit, centred to avoid loss of precision at Unix-epoch scale."""
    if len(points) < 8 or points[-1].media_time - points[0].media_time < 5:
        raise TelemetryError("Too few reliable GPS timestamps (need at least 8 over 5 seconds).")
    offsets = [p.utc - p.media_time for p in points]
    mid = median(offsets)
    mad = median(abs(o - mid) for o in offsets)
    keep = [p for p, o in zip(points, offsets) if abs(o - mid) <= max(.12, 8 * mad)]
    if len(keep) < max(8, .7 * len(points)):
        raise TelemetryError("GPS clock jumps or unreliable timestamps; automatic alignment is unsafe.")
    for _ in range(3):
        mx = sum(p.media_time for p in keep) / len(keep)
        base = keep[0].utc
        my = sum(p.utc - base for p in keep) / len(keep)
        variance = sum((p.media_time - mx) ** 2 for p in keep)
        if variance == 0:
            raise TelemetryError("GPS clock has no time span")
        slope = sum((p.media_time - mx) * (p.utc - base - my) for p in keep) / variance
        origin = base + my - slope * mx
        residuals = [abs(p.utc - (origin + slope * p.media_time)) for p in keep]
        threshold = max(.015, 6 * median(residuals))
        new = [p for p, r in zip(keep, residuals) if r <= threshold]
        if len(new) < max(8, .7 * len(points)):
            raise TelemetryError("GPS timing is too inconsistent to synchronise reliably")
        if len(new) == len(keep):
            break
        keep = new
    residuals = [abs(p.utc - (origin + slope * p.media_time)) for p in keep]
    if not .999 <= slope <= 1.001:
        raise TelemetryError("Video and GPS clocks run at different speeds; time-lapse or edited video is unsupported.")
    p95 = percentile(residuals, .95) * 1000
    if p95 > 100:
        raise TelemetryError(f"GPS timing uncertainty is excessive ({p95:.0f} ms).")
    if any(b.utc <= a.utc or b.media_time <= a.media_time for a, b in zip(keep, keep[1:])):
        raise TelemetryError("GPS timestamps are not strictly increasing")
    return Clock(origin, slope, len(keep), len(points) - len(keep), p95,
                 keep[0].media_time, keep[-1].media_time, method)


@dataclass
class Orientation:
    clockwise: int | None
    confidence: float
    evidence: str
    gravity: list[float] = field(default_factory=list)


def detect_orientation(gravity: list[tuple], image_quaternions: list[tuple],
                       display_rotation: float | None = None) -> Orientation:
    if display_rotation is not None and abs(display_rotation) > .1:
        correction = round(-display_rotation / 90) * 90 % 360
        if abs(((display_rotation + correction + 180) % 360) - 180) > 1:
            return Orientation(None, 0, "Non-right-angle display transform; review needed")
        return Orientation(correction, 1, "MP4 display rotation; verify the preview")
    valid = [v for v in gravity if len(v) == 3 and all(math.isfinite(a) for a in v)
             and .8 < math.sqrt(sum(a * a for a in v)) < 1.2
             and math.hypot(v[0], v[1]) > .65]
    if len(valid) < 20:
        return Orientation(None, 0, "Insufficient gravity evidence; inspect preview and choose rotation")
    # GoPro GRAV's horizontal and vertical axes: +X right and +Y up.
    # Conservative: nontrivial IORI can rotate the encoded frame relative to the camera.
    qs = [min(1, abs(q[0])) for q in image_quaternions if len(q) == 4]
    if qs and median(qs) < math.cos(math.radians(15) / 2):
        return Orientation(None, 0, "Image stabilisation/orientation transform needs visual review")
    votes = []
    for x, y, _ in valid:
        angle = math.degrees(math.atan2(x, -y)) % 360
        right = round(angle / 90) * 90 % 360
        if abs((angle - right + 180) % 360 - 180) <= 25:
            votes.append(right)
    if not votes:
        return Orientation(None, 0, "Camera is between right-angle orientations; review needed")
    winner, count = Counter(votes).most_common(1)[0]
    confidence = count / len(valid)
    vector = [median(v[i] for v in valid) for i in range(3)]
    if confidence < .85:
        return Orientation(None, confidence, "Orientation changes during recording; review needed", vector)
    return Orientation(winner, confidence, "Consistent GoPro gravity samples; verify the preview", vector)


@dataclass
class Video:
    path: Path
    duration: float
    width: int
    height: int
    fps: str
    codec: str
    video_start: float
    clock: Clock
    orientation: Orientation
    gps: list[GPSPoint] = field(repr=False)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"file": self.path.name, "duration_seconds": self.duration,
                "dimensions": [self.width, self.height], "fps": self.fps, "codec": self.codec,
                "utc_start": utc_text(self.clock.utc(0)), "utc_end": utc_text(self.clock.utc(self.duration)),
                "clock": asdict(self.clock), "orientation": asdict(self.orientation), "warnings": self.warnings}


def inspect_video(path: Path, metadata: dict | None = None, cancel=None) -> Video:
    metadata = metadata or probe(path)
    v = next((s for s in metadata["streams"] if s["codec_type"] == "video"), None)
    tracks = [s for s in metadata["streams"] if s.get("codec_tag_string") == "gpmd"]
    if not v or len(tracks) != 1:
        raise TelemetryError("Expected a video and one GoPro GPMF telemetry track")
    duration = float(v.get("duration", metadata["format"]["duration"]))
    start = float(v.get("start_time", 0))
    if duration <= 0 or not math.isfinite(duration):
        raise TelemetryError("Invalid video duration")
    packets = json.loads(run([executable("ffprobe"), "-v", "error", "-select_streams", str(tracks[0]["index"]),
        "-show_packets", "-show_entries", "packet=pts_time,duration_time,pos,size", "-of", "json", str(path)]))["packets"]
    batches, gravity, quats, video_bases = [], [], [], []
    file_size = path.stat().st_size
    with path.open("rb") as file:
        for packet in packets:
            if cancel and cancel.is_set():
                raise InterruptedError("Cancelled")
            pos, size = int(packet["pos"]), int(packet["size"])
            if pos < 0 or size < 8 or size > 16 * 1024 * 1024 or pos + size > file_size:
                raise TelemetryError("Invalid GPMF packet bounds")
            file.seek(pos)
            payload = file.read(size)
            pts = float(packet["pts_time"])
            pdur = float(packet.get("duration_time", 1))
            for stream in streams(payload):
                d = {r.key: r for r in stream}
                stamp = d["STMP"].values()[0][0] / 1e6 if "STMP" in d else None
                if "SHUT" in d and stamp is not None:
                    video_bases.append(stamp - pts)
                if "GRAV" in d:
                    gravity.extend(scaled(d["GRAV"], d)[::5])
                if "IORI" in d:
                    quats.extend(scaled(d["IORI"], d)[::5])
                if "GPS9" in d:
                    batches.append(("GPS9", pts, pdur, stamp, scaled(d["GPS9"], d), None))
                elif "GPS5" in d and "GPSU" in d:
                    fix = d["GPSF"].values()[0][0] if "GPSF" in d else 0
                    dop = d["GPSP"].values()[0][0] / 100 if "GPSP" in d else 100
                    text = d["GPSU"].text()
                    try:
                        utc = datetime.strptime(text, "%y%m%d%H%M%S.%f").replace(tzinfo=UTC).timestamp()
                    except ValueError:
                        continue
                    batches.append(("GPS5", pts, pdur, stamp, scaled(d["GPS5"], d), (utc, fix, dop)))
    if not batches:
        raise TelemetryError("No GPS9 or GPS5/GPSU timestamps. Enable GPS in the camera; HERO12 has no GPS receiver.")
    # HERO11 contains both GPS5 and GPS9; prefer GPS9 per-sample UTC and fix quality.
    family = "GPS9" if any(b[0] == "GPS9" for b in batches) else "GPS5"
    base = median(video_bases) if video_bases else None
    if base is not None and percentile([abs(x - base) for x in video_bases], .95) > .05:
        raise TelemetryError("Sensor-to-video clock changes during the recording")
    points, anchors = [], []
    used_precise = base is not None and all(b[3] is not None for b in batches if b[0] == family)
    for kind, pts, pdur, stamp, rows, legacy in batches:
        if kind != family or not rows:
            continue
        first_utc = EPOCH2000 + rows[0][5] * 86400 + rows[0][6] if kind == "GPS9" else legacy[0]
        first_media = stamp - base - start if used_precise else pts - start
        batch_points = []
        for i, row in enumerate(rows):
            if kind == "GPS9":
                if len(row) != 9:
                    raise TelemetryError("GPS9 sample does not have 9 fields")
                lat, lon, _, speed, _, day, sec, dop, fix = row
                utc = EPOCH2000 + day * 86400 + sec
                if not (0 <= day <= 73050 and 0 <= sec < 86400):
                    continue
                media_time = first_media + (utc - first_utc) if used_precise else first_media + i * pdur / len(rows)
            else:
                lat, lon, _, speed, _ = row
                utc = legacy[0] + i * pdur / len(rows)
                fix, dop = legacy[1:]
                media_time = first_media + i * pdur / len(rows)
            if fix < 2 or dop <= 0 or dop > 5 or not all(math.isfinite(a) for a in [utc,lat,lon,speed]):
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= speed < 200):
                continue
            point = GPSPoint(media_time, utc, lat, lon, speed * 3.6)
            batch_points.append(point)
        if batch_points:
            anchors.append(batch_points[0])
            points.extend(batch_points)
    method = family + (" + STMP aligned to video-frame SHUT" if used_precise else " + MP4 packet timing (lower precision)")
    clock = fit_clock(anchors, method)
    rotation = next((s["rotation"] for s in v.get("side_data_list", []) if "rotation" in s), None)
    orientation = detect_orientation(gravity, quats, rotation)
    warnings = []
    if clock.first_fix > 5:
        warnings.append(f"First reliable GPS fix at {clock.first_fix:.1f}s; clock extrapolated before that point.")
    if clock.last_fix < duration - 10:
        warnings.append(f"Last reliable GPS fix at {clock.last_fix:.1f}s; clock extrapolated afterwards.")
    if not used_precise:
        warnings.append("Packet timing has lower precision; check video alignment visually.")
    if clock.rejected:
        warnings.append(f"Rejected {clock.rejected} inconsistent GPS clock anchors.")
    return Video(path, duration, v["width"], v["height"], v["avg_frame_rate"], v["codec_name"],
                 start, clock, orientation, points, warnings)


def rotation_filter(clockwise: int) -> list[str]:
    return {0: [], 90: ["transpose=clock"], 180: ["hflip", "vflip"], 270: ["transpose=cclock"]}[clockwise % 360]


def preview(path: Path, seconds: float, clockwise: int, destination: Path, width: int = 800,
            max_height: int | None = None, *, crop=None, output_size=None):
    # Windows storage cleanup can remove an idle preview cache while the app is open.
    destination.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale={width}:{max_height}:force_original_aspect_ratio=decrease" if max_height else f"scale={width}:-2"
    if output_size:
        scale = f"scale={output_size[0]}:{output_size[1]}:flags=lanczos"
    filters = rotation_filter(clockwise) + ([crop.filter()] if crop else []) + [scale, "setsar=1"]
    run([executable("ffmpeg"), "-v", "error", "-nostdin", "-noautorotate", "-ss", str(max(0, seconds)),
         "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-vf", ",".join(filters),
         "-update", "1", "-y", str(destination)], timeout=90)
