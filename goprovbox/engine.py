"""Folder matching, transactional exports, verification, and readable reports."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from statistics import median
from threading import Event, Thread
import hashlib
import html
import json
import math
import os
import queue
import re
import shutil
import subprocess
import time
import uuid

from . import __version__
from .gpmf import TelemetryError
from .media import Video, inspect_video, probe, utc_text, executable, run, preview, rotation_filter, CREATE_NO_WINDOW, percentile
from .vbo import VBO, Row, read_vbo, write_vbo
from .crop import MODES as CROP_MODES, Reference, rectangle, reference_for

CT_COMPATIBILITY_NOTE = ("Extended testing encountered a Circuit Tools 3 VBO authenticity/checksum error, followed by the application closing. "
                         "Video playback, rotation and seeking were successful, but this intermittent VBO rejection remains unresolved.")


@dataclass
class Match:
    vbo: VBO
    video: Video
    rows: list[Row]
    position_median_m: float | None
    speed_median_kmh: float | None
    warnings: list[str] = field(default_factory=list)

    def summary(self):
        return {"vbo": self.vbo.path.name, "video": self.video.path.name,
                "utc_start": utc_text(self.rows[0].utc), "utc_end": utc_text(self.rows[-1].utc),
                "overlap_seconds": self.rows[-1].utc - self.rows[0].utc, "samples": len(self.rows),
                "video_start_seconds": self.video.clock.media_time(self.rows[0].utc),
                "video_end_seconds": self.video.clock.media_time(self.rows[-1].utc),
                "position_median_m": self.position_median_m, "speed_median_kmh": self.speed_median_kmh,
                "warnings": self.warnings}


@dataclass
class Scan:
    folder: Path
    videos: list[Video]
    vbos: list[VBO]
    matches: list[Match]
    ignored: list[str]
    errors: list[str]
    fingerprints: dict[str, dict]
    crop_references: dict[str, Reference] = field(default_factory=dict)
    crop_errors: dict[str, str] = field(default_factory=dict)

    def summary(self):
        return {"version": __version__, "source_folder": str(self.folder),
                "videos": [v.summary() for v in self.videos],
                "matches": [m.summary() for m in self.matches],
                "unmatched_vbo": [v.path.name for v in self.vbos if not any(m.vbo is v for m in self.matches)],
                "unmatched_video": [v.path.name for v in self.videos if not any(m.video is v for m in self.matches)],
                "ignored": self.ignored, "errors": self.errors, "source_fingerprints": self.fingerprints,
                "crop_references": {name: ref.summary() for name, ref in self.crop_references.items()},
                "crop_unavailable": self.crop_errors}


def distance(lat1, lon1, lat2, lon2):
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.hypot(x, y)


def select_for_export(scan: Scan, names: set[str]) -> Scan:
    """Restrict export inputs without trimming full-session telemetry history."""
    names = set(names)
    if not names:
        raise TelemetryError("Tick at least one video to include in processing")
    available = {m.video.path.name for m in scan.matches}
    if names - available:
        raise TelemetryError("Selected videos have no verified match: " + ", ".join(sorted(names - available)))
    matches = [m for m in scan.matches if m.video.path.name in names]
    vbos = [v for v in scan.vbos if any(m.vbo is v for m in matches)]
    sources = names | {v.path.name for v in vbos}
    return replace(scan, videos=[v for v in scan.videos if v.path.name in names], vbos=vbos,
                   matches=matches, fingerprints={n:f for n,f in scan.fingerprints.items() if n in sources},
                   crop_references={n:r for n,r in scan.crop_references.items() if n in names},
                   crop_errors={n:e for n,e in scan.crop_errors.items() if n in names})


def check_pair(vbo: VBO, video: Video, rows: list[Row]):
    times = [r.utc for r in rows]
    latidx, lonidx, speedidx = vbo.index("lat", "latitude"), vbo.index("long", "longitude"), vbo.index("velocity", "speed")
    errors, speeds = [], []
    for point in video.gps[::10]:
        j = bisect_left(times, point.utc)
        if j == 0 or j == len(rows):
            continue
        a, b = rows[j - 1], rows[j]
        if b.utc - a.utc > .5:
            continue
        fraction = (point.utc - a.utc) / (b.utc - a.utc)
        def value(idx):
            return float(a.values[idx]) * (1 - fraction) + float(b.values[idx]) * fraction
        # Racelogic uses signed minutes, positive longitude WEST.
        errors.append(distance(point.lat, point.lon, value(latidx) / 60, -value(lonidx) / 60))
        speeds.append(abs(point.speed - value(speedidx)))
    if len(errors) < 5:
        return None, None, ["Too little concurrent GPS to verify the vehicle/location independently."]
    pos, speed = median(errors), median(speeds)
    if pos > 40 or speed > 20:
        raise TelemetryError(f"GPS time overlaps but tracks disagree (median {pos:.1f}m, {speed:.1f}km/h). "
                             "Files may be from different vehicles or clocks.")
    warnings = []
    if pos > 10 or speed > 5:
        warnings.append(f"Check alignment visually: GPS differs by {pos:.1f}m / {speed:.1f}km/h median.")
    return pos, speed, warnings


def intersections(vbo: VBO, video: Video, min_seconds=2.0) -> list[Match]:
    lo, hi = video.clock.utc(0), video.clock.utc(video.duration)
    rows = [r for r in vbo.rows if lo <= r.utc < hi]
    if not rows:
        return []
    # Gaps in telemetry must never be filled or silently bridged.
    intervals = [b.utc - a.utc for a, b in zip(vbo.rows, vbo.rows[1:])]
    gap = max(.5, 10 * median(intervals))
    chunks = [[]]
    for row in rows:
        if chunks[-1] and row.utc - chunks[-1][-1].utc > gap:
            chunks.append([])
        chunks[-1].append(row)
    result = []
    for chunk in chunks:
        if chunk[-1].utc - chunk[0].utc < min_seconds:
            continue
        pos, speed, warnings = check_pair(vbo, video, chunk)
        result.append(Match(vbo, video, chunk, pos, speed, warnings + vbo.warnings))
    return result


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    # Full hashes for small data files; sampled media hashes plus size/mtime protect
    # reruns from ordinary changes without repeatedly reading many gigabytes.
    digest = hashlib.sha256()
    with path.open("rb") as file:
        if stat.st_size <= 32 * 1024 * 1024:
            digest.update(file.read())
        else:
            for pos in (0, stat.st_size // 2, max(0, stat.st_size - 1048576)):
                file.seek(pos); digest.update(file.read(1048576))
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256_sampled": digest.hexdigest()}


def scan_folder(folder: Path, log=lambda msg: None, cancel: Event | None = None) -> Scan:
    folder = folder.resolve()
    if not folder.is_dir():
        raise TelemetryError(f"Folder does not exist: {folder}")
    executable("ffmpeg"); executable("ffprobe")
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
    vbos, videos, ignored, errors, fingerprints = [], [], [], [], {}
    for path in files:
        if cancel and cancel.is_set():
            raise InterruptedError("Cancelled")
        if path.suffix.lower() not in (".vbo", ".mp4", ".mov"):
            continue
        if path.name.lower().startswith("gopro_"):
            ignored.append(path.name + " (generated output)")
            continue
        try:
            if path.suffix.lower() == ".vbo":
                log(f"Reading {path.name}")
                vbos.append(read_vbo(path))
            else:
                if re.match(r"VBOX\d+_\d+\.", path.name, re.I):
                    ignored.append(path.name + " (original VBOX video)")
                    continue
                log(f"Checking {path.name}")
                metadata = probe(path)
                if not any(s.get("codec_tag_string") == "gpmd" for s in metadata.get("streams", [])):
                    ignored.append(path.name + " (no GoPro telemetry)")
                    continue
                video = inspect_video(path, metadata, cancel)
                videos.append(video)
                log(f"  GPS: {utc_text(video.clock.origin)}; rotation: {video.orientation.clockwise}")
            fingerprints[path.name] = fingerprint(path)
        except InterruptedError:
            raise
        except (TelemetryError, OSError, KeyError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            log(f"  Could not use {path.name}: {exc}")
    matches = []
    for video in videos:
        for vbo in vbos:
            try:
                matches.extend(intersections(vbo, video))
            except TelemetryError as exc:
                errors.append(f"{video.path.name} / {vbo.path.name}: {exc}")
    scan = Scan(folder, videos, vbos, matches, ignored, errors, fingerprints)
    cache = {}
    for video in videos:
        if cancel and cancel.is_set():
            raise InterruptedError("Cancelled")
        linked = [m for m in matches if m.video is video]
        if not linked:
            continue
        try:
            scan.crop_references[video.path.name] = reference_for(linked, cache)
        except (TelemetryError, OSError, ValueError) as exc:
            scan.crop_errors[video.path.name] = str(exc)
    return scan


def recording_groups(videos: list[Video]) -> list[list[Video]]:
    groups = []
    for video in sorted(videos, key=lambda v: v.clock.origin):
        name = re.fullmatch(r"G([HX])(\d{2})(\d{4})", video.path.stem, re.I)
        found = False
        for group in groups:
            prev = group[-1]
            old = re.fullmatch(r"G([HX])(\d{2})(\d{4})", prev.path.stem, re.I)
            if name and old and name[1].upper() == old[1].upper() and name[3] == old[3] and int(name[2]) == int(old[2]) + 1:
                gap = video.clock.origin - prev.clock.utc(prev.duration)
                if abs(gap) <= .25:
                    group.append(video); found = True; break
        if not found:
            groups.append([video])
    return groups


def dimensions(video: Video, rotation: int, max_size: int, crop=None):
    w, h = (video.height, video.width) if rotation % 180 else (video.width, video.height)
    if crop:
        w, h = crop.width, crop.height
    factor = min(1, max_size / max(w, h)) if max_size else 1
    return max(2, round(w * factor / 2) * 2), max(2, round(h * factor / 2) * 2)


def encode_args(video: Video, destination: Path, rotation: int, max_size: int, backend: str, overlay=None, crop=None):
    if backend == "copy":
        if overlay is not None or crop is not None:
            raise TelemetryError("Drawing gauges or cropping requires re-encoding; choose HD, Full resolution or Compact")
        return [executable("ffmpeg"), "-hide_banner", "-nostdin", "-n", "-display_rotation", str(-rotation),
                "-noautorotate", "-i", str(video.path), "-map", "0:v:0", "-map", "0:a?",
                "-c", "copy", "-map_metadata", "0", "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", str(destination)]
    w, h = dimensions(video, rotation, max_size, crop)
    args = [executable("ffmpeg"), "-hide_banner", "-nostdin", "-y"]
    if backend == "qsv" and crop is None:
        args += ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
    args += ["-noautorotate", "-i", str(video.path)]
    if overlay is not None:
        args += ["-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{overlay.size[0]}x{overlay.size[1]}",
                 "-framerate", video.fps, "-i", "pipe:0"]
    args += ["-map", "[with_data]" if overlay is not None else "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1"]
    if backend == "qsv" and crop is None:
        filt = f"vpp_qsv=w={w}:h={h}:format=nv12:out_range=limited"
        if rotation:
            filt += ":transpose=" + {90: "clock", 180: "reversal", 270: "cclock"}[rotation]
        codec_args = ["-c:v", "h264_qsv", "-global_quality", "20", "-look_ahead", "0"]
        if overlay is not None:
            filt += ",hwdownload,format=nv12"
    else:
        # Crop upright CPU frames, then retain QSV encoding where available. This
        # avoids driver-dependent QSV crop/transpose ordering and 10-bit surfaces.
        filters = rotation_filter(rotation) + ([crop.filter()] if crop else []) + [f"scale={w}:{h}:flags=lanczos:out_range=tv", "setsar=1", "format=nv12" if backend == "qsv" else "format=yuv420p"]
        filt = ",".join(filters)
        codec_args = (["-c:v", "h264_qsv", "-global_quality", "20", "-look_ahead", "0"] if backend == "qsv"
                      else ["-c:v", "libx264", "-preset", "fast", "-crf", "18"])
    if overlay is not None:
        x, y = overlay.position
        args += ["-filter_complex", f"[0:v:0]{filt}[base];[base][1:v:0]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0:format=auto,format=nv12[with_data]"]
    else:
        args += ["-vf", filt]
    args += codec_args
    # Preserve frame cadence; one-second GOP makes Circuit Tools seeking responsive.
    args += ["-fps_mode", "passthrough", "-g", str(max(1, round(float(Fraction(video.fps))))),
             "-c:a", "aac", "-b:a", "192k", "-metadata:s:v:0", "rotate=0",
             "-color_range", "tv", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(destination)]
    return args


def encode(video: Video, destination: Path, rotation: int, max_size: int, encoder: str,
           log, progress, cancel: Event, overlay=None, crop=None) -> str:
    backends = ["qsv", "software"] if encoder == "auto" and os.name == "nt" else ["software" if encoder == "auto" else encoder]
    for backend in backends:
        log(f"Preparing {video.path.name}: {backend}, rotation {rotation}°, {dimensions(video, rotation, max_size, crop)}")
        logfile = destination.with_suffix(".encoding.log")
        with logfile.open("wb") as err:
            process = subprocess.Popen(encode_args(video, destination, rotation, max_size, backend, overlay, crop),
                stdin=subprocess.PIPE if overlay is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=err, creationflags=CREATE_NO_WINDOW)
            overlay_errors = []
            painter = None
            if overlay is not None:
                painter = Thread(target=overlay.feed, args=(process.stdin, cancel, overlay_errors), daemon=True)
                painter.start()
            messages = queue.Queue()
            def reader():
                for line in iter(process.stdout.readline, b""):
                    messages.put(line.decode("utf-8", "replace").strip())
                messages.put(None)
            thread = Thread(target=reader, daemon=True); thread.start()
            last_output = time.monotonic()
            try:
                while process.poll() is None or not messages.empty():
                    if cancel.is_set():
                        raise InterruptedError("Cancelled; originals are unchanged")
                    try:
                        line = messages.get(timeout=.25)
                    except queue.Empty:
                        if time.monotonic() - last_output > 180:
                            raise TelemetryError("Video encoder stopped reporting progress for three minutes")
                        continue
                    if line is not None:
                        last_output = time.monotonic()
                        if line.startswith("out_time_us="):
                            try:
                                progress(min(.999, max(0, int(line.split("=")[1]) / 1e6 / video.duration)))
                            except ValueError:
                                pass
                process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                thread.join(timeout=2)
                if painter is not None:
                    painter.join(timeout=5)
                process.stdout.close()
        if process.returncode == 0:
            if overlay_errors:
                raise TelemetryError(f"Could not finish drawing telemetry: {overlay_errors[0]}")
            progress(1)
            return backend
        reason = logfile.read_text(encoding="utf-8", errors="replace")[-2000:]
        if backend == backends[-1]:
            raise TelemetryError("Video preparation failed: " + reason)
        log("Hardware acceleration unavailable; switching to the software encoder.")
    raise AssertionError("No encoder selected")


def validate_video(video: Video, destination: Path, rotation: int, max_size: int, crop=None):
    meta = probe(destination)
    v = next(s for s in meta["streams"] if s["codec_type"] == "video")
    expected = dimensions(video, rotation, max_size, crop)
    if v.get("codec_name") != "h264" or (v["width"], v["height"]) != expected:
        raise TelemetryError("Encoded video has an unexpected format or size")
    if abs(float(v["duration"]) - video.duration) > max(.07, 2 / float(Fraction(video.fps))):
        raise TelemetryError("Encoded video duration differs from the source")
    if abs(float(v.get("start_time", 0))) > .002:
        raise TelemetryError("Encoded video does not start at time zero")
    if Fraction(v["avg_frame_rate"]) != Fraction(video.fps):
        raise TelemetryError("Encoded video frame rate differs from the source")
    return {"codec": v["codec_name"], "dimensions": list(expected), "frames": v.get("nb_frames"),
            "duration": v["duration"], "size_bytes": destination.stat().st_size}


def safe_stem(value: str):
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:80]


def export(scan: Scan, output: Path | None = None, *, rotations: dict[str, int] | None = None,
           max_size: int = 1920, encoder="auto", video_mode="convert", log=lambda msg: None,
           progress=lambda value: None, cancel: Event | None = None,
           telemetry_overlay: bool = False, overlay_scene: Path | None = None, overlay_mode="four",
           include_videos: set[str] | None = None, crops: dict[str, str] | None = None) -> Path:
    cancel = cancel or Event()
    rotations = rotations or {}
    crops = crops or {}
    if any(mode not in CROP_MODES for mode in crops.values()):
        raise TelemetryError("Unknown crop option")
    if set(crops) - {v.path.name for v in scan.videos}:
        raise TelemetryError("Crop option refers to an unknown GoPro file")
    telemetry_overlay = telemetry_overlay or overlay_scene is not None or overlay_mode == "full"
    if video_mode != "convert":
        raise TelemetryError("Original-video mode has been removed because Circuit Tools compatibility is unresolved. Export requires re-encoding.")
    if overlay_mode not in ("four", "full"):
        raise TelemetryError("Unknown overlay mode")
    if telemetry_overlay and overlay_mode == "full" and overlay_scene is None:
        raise TelemetryError("Choose a VBOX scene for the full-scene overlay")
    unknown = set(rotations) - {v.path.name for v in scan.videos}
    if unknown:
        raise TelemetryError("Rotation override refers to an unknown GoPro file: " + ", ".join(sorted(unknown)))
    excluded_videos = []
    if include_videos is not None:
        included = set(include_videos)
        excluded_videos = [v.path.name for v in scan.videos if v.path.name not in included]
        scan = select_for_export(scan, included)
        rotations = {name:value for name,value in rotations.items() if name in included}
        crops = {name:value for name,value in crops.items() if name in included}
    default_name = "GoPro Circuit Tools Overlay" if telemetry_overlay else "GoPro Circuit Tools"
    if any(mode != "none" for mode in crops.values()):
        default_name += " Cropped"
    output = (output or scan.folder / default_name).resolve()
    source_folder = scan.folder.resolve()
    if output == source_folder or output in source_folder.parents:
        raise TelemetryError("Choose a separate output folder, not the source folder or one of its parents")
    if not scan.matches:
        raise TelemetryError("No verified GPS overlap was found. See the scan results for details.")
    if encoder not in ("auto", "qsv", "software") or max_size < 0:
        raise TelemetryError("Invalid encoder or output size")
    used = [v for v in scan.videos if any(m.video is v for m in scan.matches)]
    selected = {}
    for video in used:
        choice = rotations.get(video.path.name, video.orientation.clockwise)
        if choice not in (0, 90, 180, 270):
            raise TelemetryError(f"{video.path.name}: inspect the preview and choose a rotation before exporting")
        selected[video.path.name] = choice
    settings = {"version": __version__, "rotations": selected, "max_size": max_size, "encoder": encoder,
                "included_videos": [v.path.name for v in used]}
    crop_rectangles, crop_settings, cache = {}, {}, {}
    for video in used:
        name = video.path.name
        mode = crops.get(name, "none")
        if mode == "none":
            continue
        if cancel.is_set():
            raise InterruptedError("Cancelled")
        ref = reference_for([m for m in scan.matches if m.video is video], cache)
        if name in scan.crop_references and ref != scan.crop_references[name]:
            raise TelemetryError(f"{name}: original VBOX video changed since scan; scan again")
        crop_rectangles[name] = rectangle(video.width, video.height, selected[name], ref.aspect, mode)
        crop_settings[name] = {"mode": mode, "rectangle": crop_rectangles[name].summary(), "reference": ref.summary()}
    if crop_settings:
        settings["crops"] = crop_settings
    renderers = {}
    if telemetry_overlay:
        from .overlay import load_scene, Renderer
        log("Preparing the overlay and checking its data sources")
        scene = load_scene(Path(overlay_scene), mode=overlay_mode) if overlay_scene else None
        settings["overlay"] = scene.summary() if scene else {"name": "Built-in four-channel dashboard", "speed_unit": "mph", "brake_unit": "psi"}
        settings["overlay"]["mode"] = overlay_mode
        for video in used:
            renderers[video.path.name] = Renderer(video, [m for m in scan.matches if m.video is video],
                                                *dimensions(video, selected[video.path.name], max_size, crop_rectangles.get(video.path.name)), scene)
        settings["overlay"]["notes"] = list(dict.fromkeys(note for renderer in renderers.values() for note in getattr(renderer, "notes", [])))
    signature = hashlib.sha256(json.dumps({"sources": scan.fingerprints, "settings": settings}, sort_keys=True).encode()).hexdigest()
    # Revalidate source metadata before committing to a long encode.
    for name, info in scan.fingerprints.items():
        if fingerprint(scan.folder / name) != info:
            raise TelemetryError(f"Source changed since scan: {name}; scan again")
    if output.exists():
        reportfile = output / "report.json"
        try:
            previous = json.loads(reportfile.read_text(encoding="utf-8"))
            if previous.get("signature") == signature and previous.get("status") == "complete":
                intact = all(fingerprint(output / name) == value for name, value in previous["output_fingerprints"].items())
                if intact:
                    log("This folder has already been exported and verified. Reusing the existing result.")
                    progress(1); return output
        except (OSError, ValueError, KeyError):
            pass
        raise TelemetryError(f"Output folder already exists with different settings or changed files: {output}. "
                             "Choose a new output folder; existing results are never overwritten.")
    groups = recording_groups(used)
    output.parent.mkdir(parents=True, exist_ok=True)
    estimate = sum(v.duration for v in used) * 8_000_000 + 512 * 1024 * 1024
    if shutil.disk_usage(output.parent).free < estimate:
        raise TelemetryError(f"Allow about {estimate / 1024**3:.1f} GB free for video preparation.")
    staging = output.parent / ("." + output.name + ".working-" + uuid.uuid4().hex[:8])
    staging.mkdir()
    report = scan.summary() | {"signature": signature, "settings": settings, "status": "working", "outputs": [], "media": [],
                              "excluded_videos": excluded_videos,
                              "compatibility_notes": [CT_COMPATIBILITY_NOTE]}
    names = set()
    try:
        duration_total = sum(v.duration for v in used)
        duration_done = 0
        for group in groups:
            prefix = "GoPro_" + safe_stem(group[0].path.stem) + "_"
            avi_prefix = prefix
            video_indices, video_files = {}, {}
            for index, video in enumerate(group, 1):
                if cancel.is_set():
                    raise InterruptedError("Cancelled")
                filename = f"{prefix}{index:04d}.mp4"
                if filename.casefold() in names:
                    raise TelemetryError("Input filenames produce conflicting output names")
                names.add(filename.casefold())
                dest = staging / filename
                preview_name = dest.stem + ".jpg"
                rotation = selected[video.path.name]
                backend = encode(video, dest, rotation, max_size, encoder, log,
                                 lambda p: progress((duration_done + p * video.duration) / duration_total * .93), cancel,
                                 overlay=renderers.get(video.path.name), crop=crop_rectangles.get(video.path.name))
                checked = validate_video(video, dest, rotation, max_size, crop_rectangles.get(video.path.name))
                video_indices[video.path.name] = index
                video_files[index] = filename
                preview_time = min(30, video.duration / 2)
                if telemetry_overlay:
                    first_match = next(m for m in scan.matches if m.video is video)
                    preview_time = video.clock.media_time((first_match.rows[0].utc + first_match.rows[-1].utc) / 2)
                preview(dest, preview_time, 0, staging / preview_name)
                report["media"].append({"file": filename, "source": video.path.name, "rotation_clockwise": selected[video.path.name],
                                         "encoder": backend, "preview": preview_name, "verification": checked,
                                         "crop": crop_settings.get(video.path.name)})
                duration_done += video.duration
            for vbo in scan.vbos:
                entries = []
                for video in group:
                    index = video_indices[video.path.name]
                    for match in scan.matches:
                        if match.video is video and match.vbo is vbo:
                            entries.extend((row, video.clock.media_time(row.utc), index) for row in match.rows)
                if not entries:
                    continue
                entries.sort(key=lambda entry: (entry[0].utc, entry[2]))
                # Adjacent chapters can share a boundary sample; keep it once.
                unique = []
                for entry in entries:
                    if not unique or entry[0].utc > unique[-1][0].utc:
                        unique.append(entry)
                chunks = [[]]
                gap_limit = max(.5, 10 * median(b.utc - a.utc for a, b in zip(vbo.rows, vbo.rows[1:])))
                for entry in unique:
                    if chunks[-1] and entry[0].utc - chunks[-1][-1][0].utc > gap_limit:
                        chunks.append([])
                    chunks[-1].append(entry)
                for part, chunk in enumerate(chunks, 1):
                    basename = prefix.rstrip("_") + "_" + safe_stem(vbo.path.stem)
                    if len(chunks) > 1:
                        basename += f"_part{part:02d}"
                    filename = basename + ".vbo"
                    if filename.casefold() in names:
                        raise TelemetryError("Conflicting VBO output filenames")
                    names.add(filename.casefold())
                    rs, ts, indices = map(list, zip(*chunk))
                    write_vbo(vbo, rs, ts, avi_prefix, staging / filename, indices)
                    verify_vbo(staging / filename, vbo, rs, ts, indices, {video_indices[v.path.name]: v for v in group})
                    report["outputs"].append({"file": filename, "source": vbo.path.name, "samples": len(rs),
                        "utc_start": utc_text(rs[0].utc), "utc_end": utc_text(rs[-1].utc),
                        "overlap_seconds": rs[-1].utc - rs[0].utc,
                        "videos": [video_files[idx] for idx in sorted(set(indices))],
                        "telemetry_preserved": True})
        for name, info in scan.fingerprints.items():
            if fingerprint(scan.folder / name) != info:
                raise TelemetryError(f"Source changed during export: {name}")
        for setting in crop_settings.values():
            for name, stamp in setting["reference"]["sources"].items():
                stat = (scan.folder / name).stat()
                if {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns} != stamp:
                    raise TelemetryError(f"Original VBOX video changed during export: {name}")
        report["status"] = "complete"
        report["output_fingerprints"] = {p.name: fingerprint(p) for p in staging.iterdir() if p.suffix in (".vbo", ".mp4")}
        (staging / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        write_report(report, staging / "Report.html")
        (staging / "OPEN IN CIRCUIT TOOLS.txt").write_text(
            "Open the GoPro_*.vbo files in Circuit Tools 3. Keep generated MP4 files beside them.\n"
            "Original telemetry channels and lap markers are retained. Only overlapping telemetry is exported.\n"
            "Video covers the full GoPro chapter; VBOX video times seek into the matching portion.\n"
            "See Report.html for matches, orientation, skipped files and validation.\n", encoding="utf-8")
        if cancel.is_set():
            raise InterruptedError("Cancelled")
        staging.rename(output)
        progress(1); log(f"Complete: {output}")
        return output
    except BaseException as exc:
        report["status"] = "cancelled" if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else "failed"
        report["failure"] = str(exc)
        (staging / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        log(f"Incomplete work and diagnostic logs saved in {staging}")
        raise


def verify_vbo(path: Path, source: VBO, rows: list[Row], times: list[float], indices: list[int], videos: list[Video] | dict[int, Video]):
    reread = read_vbo(path)
    if reread.columns != source.columns or len(reread.rows) != len(rows):
        raise TelemetryError("Exported VBO schema or sample count changed")
    vi, ti = source.index("avifileindex"), source.index("avitime", "avisynctime")
    for actual, expected, video_time, index in zip(reread.rows, rows, times, indices):
        if actual.utc != expected.utc:
            raise TelemetryError("Exported VBOX GPS time changed")
        if any(a != e for i, (a, e) in enumerate(zip(actual.values, expected.values)) if i not in (vi, ti)):
            raise TelemetryError("An original telemetry channel changed during export")
        t = int(actual.values[ti]) / 1000
        video = videos[index] if isinstance(videos, dict) else videos[index - 1]
        if int(actual.values[vi]) != index or abs(t - video_time) > .000501 or not 0 <= t < video.duration:
            raise TelemetryError("Invalid exported video reference")


def write_report(report: dict, path: Path):
    e = html.escape
    def crop_description(media):
        crop = media.get("crop")
        if not crop:
            return "No crop"
        mode = {"top": "cut off top", "bottom": "cut off bottom", "centre": "centre crop"}[crop["mode"]]
        return "VBOX " + crop["reference"]["aspect"] + " · " + mode
    rows = "".join(f'<tr><td><a href="{e(o["file"])}">{e(o["file"])}</a></td><td>{o["overlap_seconds"] / 60:.2f} min</td>'
                   f'<td>{o["samples"]:,}</td><td>{e(o["utc_start"])}<br>{e(o["utc_end"])}</td></tr>' for o in report["outputs"])
    cards = "".join(f'<article><img src="{e(m.get("preview", Path(m["file"]).stem + ".jpg"))}"><h3>{e(m["source"])}</h3>'
                    f'<p>Rotation: {m["rotation_clockwise"]}° clockwise · {m["verification"]["dimensions"][0]} × '
                    f'{m["verification"]["dimensions"][1]} · {e(m["verification"]["codec"].upper())}</p>'
                    f'<p>{e(crop_description(m))} · Encoded to H.264</p></article>' for m in report["media"])
    storage_note = "Keep their MP4 files beside them."
    warnings = report.get("compatibility_notes", []) + report["errors"] + [w for v in report["videos"] for w in v["warnings"]] + [w for m in report["matches"] for w in m["warnings"]]
    overlay_info = report.get("settings", {}).get("overlay")
    if overlay_info:
        scope = "Full scene without cameras" if overlay_info.get("mode") == "full" else "Speed, RPM, throttle and brake"
        warnings.append("Data overlay: " + overlay_info["name"] + ". " + scope + ". Readouts are interpolated between valid samples and hidden outside data coverage or across gaps longer than 0.5 seconds.")
        warnings.extend(overlay_info.get("notes", []))
        if overlay_info.get("omitted_elements"):
            warnings.append("Scene elements omitted: " + ", ".join(overlay_info["omitted_elements"]))
    if report.get("excluded_videos"):
        warnings.append("Not selected for processing: " + ", ".join(report["excluded_videos"]))
    notes = "".join(f"<li>{e(w)}</li>" for w in dict.fromkeys(warnings)) or "<li>No additional issues.</li>"
    details = "".join(f'<tr><td>{e(m["video"])}</td><td>{e(m["vbo"])}</td><td>{m["position_median_m"]:.2f} m</td>'
                      f'<td>{m["speed_median_kmh"]:.2f} km/h</td></tr>' for m in report["matches"] if m["position_median_m"] is not None)
    path.write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>GoPro VBOX Sync — export report</title><style>
body{{font:16px/1.6 system-ui,sans-serif;margin:0;background:#f3f5f8;color:#1b2738}}main{{max-width:1100px;margin:auto;padding:42px}}
h1{{font-size:38px;letter-spacing:-1px;margin:0}}h2{{margin-top:36px}}.eyebrow{{color:#087d70;font-weight:700;letter-spacing:2px}}
table{{width:100%;border-collapse:collapse;background:white}}th,td{{text-align:left;padding:14px;border-bottom:1px solid #dde3ea}}
a{{color:#087d70}}.cards{{display:flex;gap:24px;flex-wrap:wrap}}article{{background:white;padding:20px;flex:1;min-width:260px}}
img{{width:100%;max-width:480px}}.note{{background:#e2efea;padding:18px;border-radius:8px}}small{{color:#576375}}code{{overflow-wrap:anywhere}}
</style><main><div class="eyebrow">GOPRO VBOX SYNC · {__version__}</div><h1>Export prepared</h1>
<p>GPS-matched GoPro video with your original VBOX telemetry.</p><p class="note">Open the <b>.vbo</b> files below in Circuit Tools 3.
{storage_note} Original recordings are unchanged. Video before or after the telemetry overlap is retained;
the VBO links only to valid overlapping samples.</p><p><strong>Circuit Tools compatibility:</strong> {e(CT_COMPATIBILITY_NOTE)}</p>
<table><thead><tr><th>Open this file</th><th>Overlap</th><th>Samples</th><th>UTC interval</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Orientation & video</h2><div class="cards">{cards}</div><h2>Independent GPS checks</h2>
<p>Median differences between GoPro GPS and interpolated VBOX measurements confirm the recording match. These differences include receiver accuracy and sensor latency.</p>
<table><tr><th>GoPro</th><th>VBOX</th><th>Position</th><th>Speed</th></tr>{details}</table>
<h2>Coverage</h2><p>VBOX files without matching video: {e(', '.join(report['unmatched_vbo']) or 'None')}.</p>
<p>GoPro files without matching telemetry: {e(', '.join(report['unmatched_video']) or 'None')}.</p>
<h2>Notes</h2><ul>{notes}</ul><p>All original channel values and lap markers are retained. Only video index/time fields are replaced;
exact duplicate samples are removed. Telemetry gaps are split into separate files. UTC dates handle midnight; file modification times are never used for matching.</p>
<p>Clock residuals describe consistency, not absolute synchronisation accuracy. GPS receiver latency and video exposure may leave a small offset;
check a distinctive event visually. Source footage remains {e(report['videos'][0]['codec']) if report['videos'] else ''} in the original folder.</p>
<small>Source: <code>{e(report['source_folder'])}</code><br>Detailed measurements and fingerprints: <a href="report.json">report.json</a></small></main></html>''', encoding="utf-8")
