"""Align original VBOX sound with the GPS clock of an exported GoPro clip.

The recorder's AVI timestamps are frame-quantised. Fit a clock across a
continuous chapter instead of time-warping the sound at every data sample.
Burned-in gauge readings are never used to infer sound timing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from statistics import median
from threading import Event, Thread
import math
import os
import queue
import shutil
import subprocess
import time
import wave

from .crop import video_reference
from .gpmf import TelemetryError
from .media import CREATE_NO_WINDOW, executable, probe

RATE = 48000
CHANNELS = 2
FRAME_BYTES = CHANNELS * 2


def stamp(path):
    s = path.stat()
    return {"size": s.st_size, "mtime_ns": s.st_mtime_ns}


@dataclass
class AudioClock:
    utc: float
    media: float
    rate: float
    uncertainty: float

    def at(self, utc):
        return self.media + (utc - self.utc) * self.rate

    def inverse(self, media):
        return self.utc + (media - self.media) / self.rate


def fit_audio_clock(rows, column, video_start=0):
    if len(rows) < 8 or rows[-1].utc - rows[0].utc < 1:
        raise TelemetryError("too few original video timestamps to align sound reliably")
    base = rows[0].utc
    pairs = [(r.utc - base, float(r.values[column]) / 1000 + video_start) for r in rows]
    if any(y < video_start or not math.isfinite(y) for _, y in pairs):
        raise TelemetryError("invalid original video timestamps")
    if any(b[1] < a[1] - .08 for a, b in zip(pairs, pairs[1:])):
        raise TelemetryError("original video timestamps jump backwards")
    keep = pairs
    for _ in range(3):
        mx = sum(x for x, _ in keep) / len(keep)
        my = sum(y for _, y in keep) / len(keep)
        # Short chapters do not constrain drift well enough to estimate it.
        rate = (sum((x-mx)*(y-my) for x, y in keep) / sum((x-mx)**2 for x, _ in keep)
                if keep[-1][0] - keep[0][0] >= 10 else 1.0)
        offset = median(y - rate*x for x, y in keep)
        errors = [abs(y-offset-rate*x) for x, y in pairs]
        limit = max(.075, 6 * median(errors))
        new = [p for p, error in zip(pairs, errors) if error <= limit]
        if len(new) < .98 * len(pairs):
            raise TelemetryError("original video timestamps are inconsistent")
        if len(new) == len(keep):
            break
        keep = new
    errors = sorted(abs(y-offset-rate*x) for x, y in pairs)
    p95 = errors[int((len(errors)-1)*.95)]
    if not .995 <= rate <= 1.005 or p95 > .075 or errors[-1] > .15:
        raise TelemetryError("original video clock is unreliable")
    return AudioClock(base, offset, rate, p95)


@dataclass
class AudioSpan:
    kind: str
    path: Path | None
    first: int
    stop: int
    source_start: float = 0
    rate: float = 1

    @property
    def samples(self):
        return self.stop - self.first

    def summary(self):
        return {"source": self.path.name if self.path else None, "kind": self.kind,
                "output_start_seconds": self.first / RATE, "output_end_seconds": self.stop / RATE,
                "source_start_seconds": self.source_start, "source_seconds_per_output_second": self.rate}


@dataclass
class AudioPlan:
    samples: int
    spans: list[AudioSpan]
    sources: dict[Path, dict]
    clocks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self):
        return {"sample_rate": RATE, "channels": CHANNELS, "samples": self.samples,
                "segments": [s.summary() for s in self.spans], "clocks": self.clocks,
                "sources": {p.name: s for p, s in self.sources.items()}, "notes": self.notes}


def _metadata(path, cache):
    before = stamp(path)
    key = (path, before["size"], before["mtime_ns"])
    if key not in cache:
        meta = probe(path)
        if stamp(path) != before:
            raise TelemetryError(f"Sound source changed while reading: {path.name}")
        video = next((s for s in meta["streams"] if s["codec_type"] == "video"), None)
        audio = next((s for s in meta["streams"] if s["codec_type"] == "audio"), None)
        if video is None:
            raise TelemetryError(f"{path.name}: original recording has no video")
        if audio is not None:
            start = float(audio.get("start_time", 0))
            duration = float(audio.get("duration", meta.get("format", {}).get("duration", 0)))
            if not math.isfinite(start + duration) or duration <= 0:
                raise TelemetryError(f"{path.name}: cannot determine sound coverage")
            audio = (start, start + duration)
        cache[key] = float(video.get("start_time", 0)), audio, before
    return cache[key]


def plan_audio(video, matches, cache=None):
    """Prefer linked VBOX sound; use GoPro sound or silence in uncovered periods."""
    cache = {} if cache is None else cache
    count = round(video.duration * RATE)
    candidates, sources, clocks, notes = [], {}, [], []
    vbos = {m.vbo.path: m.vbo for m in matches}
    lo, hi = video.clock.utc(0), video.clock.utc(video.duration)
    for vbo in vbos.values():
        prefix, extension = video_reference(vbo)
        files = {p.name.casefold(): p for p in vbo.path.parent.iterdir() if p.is_file()}
        vi, ti = vbo.index("avifileindex"), vbo.index("avitime", "avisynctime")
        period = median(b.utc-a.utc for a, b in zip(vbo.rows, vbo.rows[1:]))
        gap = max(.5, 10*period)
        groups = []
        for row in vbo.rows:
            index = float(row.values[vi])
            if index != int(index) or index < 0:
                raise TelemetryError(f"{vbo.path.name}: invalid original video chapter")
            index = int(index)
            if not groups or groups[-1][0] != index or row.utc-groups[-1][1][-1].utc > gap:
                groups.append((index, []))
            groups[-1][1].append(row)
        pad = max(period, 1/float(Fraction(video.fps)))
        for i, (index, rows) in enumerate(groups):
            if not index:
                continue
            left, right = rows[0].utc-pad, rows[-1].utc+pad
            if i and rows[0].utc-groups[i-1][1][-1].utc <= gap:
                left = (rows[0].utc+groups[i-1][1][-1].utc)/2
            if i+1 < len(groups) and groups[i+1][1][0].utc-rows[-1].utc <= gap:
                right = (rows[-1].utc+groups[i+1][1][0].utc)/2
            if right <= lo or left >= hi:
                continue
            name = f"{prefix}{index:04d}.{extension}"
            path = files.get(name.casefold())
            if path is None:
                notes.append(f"{name} is missing; GoPro sound is used where available.")
                continue
            video_start, coverage, source_stamp = _metadata(path, cache)
            sources[path] = source_stamp
            if coverage is None:
                notes.append(f"{name} has no sound; GoPro sound is used where available.")
                continue
            try:
                clock = fit_audio_clock(rows, ti, video_start)
            except TelemetryError as exc:
                notes.append(f"{name}: {exc}; GoPro sound is used where available.")
                continue
            left = max(left, clock.inverse(coverage[0]), lo)
            right = min(right, clock.inverse(coverage[1]), hi)
            first = 0 if left == lo else max(0, math.ceil(video.clock.media_time(left)*RATE - .01))
            # Both sides use the same exclusive sample boundary so chapter
            # joins do not acquire a one-sample GoPro insert from rounding.
            stop = count if right == hi else min(count, math.ceil(video.clock.media_time(right)*RATE - .01))
            if stop <= first:
                continue
            candidates.append(AudioSpan("vbox", path, first, stop,
                clock.at(video.clock.utc(first/RATE)), clock.rate*video.clock.rate))
            clocks.append({"vbo": vbo.path.name, "video": name, "anchors": len(rows),
                           "utc_anchor": clock.utc, "media_anchor_seconds": clock.media,
                           "media_seconds_per_utc_second": clock.rate,
                           "residual_p95_ms": clock.uncertainty*1000})
    candidates.sort(key=lambda s: s.first)
    for a, b in zip(candidates, candidates[1:]):
        if b.first < a.stop:
            raise TelemetryError("More than one VBOX recording supplies sound at the same time; choose GoPro sound")
    spans = []
    cursor = 0
    _, fallback, source_stamp = _metadata(video.path, cache)
    sources[video.path] = source_stamp
    # Trimmed video views have a shifted GPS clock, but still use the source
    # file's original first video timestamp. Find its original clock via Match.
    original = next((m.video for m in matches if m.video.path == video.path), video)
    offset = original.video_start + original.clock.media_time(video.clock.utc(0))

    def fill(first, stop):
        if stop <= first:
            return
        a = max(first, math.ceil((fallback[0]-offset)*RATE)) if fallback else stop
        b = min(stop, math.floor((fallback[1]-offset)*RATE)) if fallback else first
        a, b = min(stop, max(first, a)), min(stop, max(first, b))
        if b <= a:
            spans.append(AudioSpan("silence", None, first, stop))
        else:
            if a > first: spans.append(AudioSpan("silence", None, first, a))
            spans.append(AudioSpan("gopro", video.path, a, b, offset+a/RATE))
            if b < stop: spans.append(AudioSpan("silence", None, b, stop))

    for span in candidates:
        fill(cursor, span.first); spans.append(span); cursor = span.stop
    fill(cursor, count)
    seconds = sum(s.samples for s in spans if s.kind == "gopro") / RATE
    silent = sum(s.samples for s in spans if s.kind == "silence") / RATE
    if seconds > .001:
        notes.append(f"GoPro sound fills {seconds:.3f} seconds without usable VBOX sound.")
    if silent > .001:
        notes.append(f"Silence fills {silent:.3f} seconds where neither recording has sound.")
    return AudioPlan(count, spans, sources, clocks, list(dict.fromkeys(notes)))


def _process(args, log_path, cancel, consume=lambda block: None):
    """Drain a subprocess without holding an entire recording in memory."""
    with log_path.open("wb") as err:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
        blocks = queue.Queue(maxsize=8)
        stopped = Event()
        def reader():
            try:
                while data := p.stdout.read(65536):
                    while not stopped.is_set():
                        try: blocks.put(data, timeout=.25); break
                        except queue.Full:
                            if stopped.is_set(): return
                while not stopped.is_set():
                    try: blocks.put(None, timeout=.25); break
                    except queue.Full: pass
            finally:
                p.stdout.close()
        worker = Thread(target=reader, daemon=True); worker.start()
        last = time.monotonic()
        try:
            while True:
                if cancel.is_set(): raise InterruptedError("Sound preparation cancelled")
                try: block = blocks.get(timeout=.25)
                except queue.Empty:
                    if time.monotonic()-last > 180: raise TelemetryError("Sound preparation stopped responding")
                    continue
                if block is None: break
                consume(block); last = time.monotonic()
            p.wait(timeout=30)
            if p.returncode:
                raise TelemetryError("Sound preparation failed: " + log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
        finally:
            stopped.set()
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: p.kill(); p.wait()
            worker.join(timeout=2)


def render_audio(plan, destination, cancel, progress=lambda _: None):
    for path, expected in plan.sources.items():
        if stamp(path) != expected: raise TelemetryError(f"Sound source changed: {path.name}")
    if plan.samples * FRAME_BYTES > 4_000_000_000:
        raise TelemetryError("Sound track is too long; export separate GoPro chapters")
    written = 0
    silence = bytes(RATE * FRAME_BYTES)
    with wave.open(str(destination), "wb") as wav:
        wav.setnchannels(CHANNELS); wav.setsampwidth(2); wav.setframerate(RATE)
        for span in plan.spans:
            if cancel.is_set(): raise InterruptedError("Sound preparation cancelled")
            expected = span.samples * FRAME_BYTES
            if span.path is None:
                remaining = expected
                while remaining:
                    if cancel.is_set(): raise InterruptedError("Sound preparation cancelled")
                    block = silence[:min(len(silence), remaining)]; wav.writeframesraw(block); remaining -= len(block)
            else:
                # High intermediate sample rate keeps clock-ratio rounding below
                # 3 ppm; resampling corrects clock drift without frame-sized jumps.
                intermediate = 192000
                adjusted = round(intermediate * span.rate)
                start = span.source_start
                end = start + span.samples/RATE * span.rate
                # Audio packets can have their own clock skew relative to the
                # video's PTS. Soft timestamp compensation prevents that skew
                # accumulating before the separate video/GPS clock correction.
                filt = (f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-{start:.9f}/TB,"
                        f"aresample={intermediate}:async=1000:min_comp=0.00002:first_pts=0,asetrate={adjusted},"
                        f"aresample={RATE},apad,atrim=end_sample={span.samples},asetpts=N/SR/TB")
                # Disable FFmpeg's implicit accurate-seek trim: with copyts and
                # nonzero format starts it can apply the start offset twice.
                # The explicit atrim above trims decoded samples by their PTS.
                args = [executable("ffmpeg"), "-v", "error", "-nostdin", "-copyts", "-noaccurate_seek", "-seek_timestamp", "1",
                        "-ss", f"{math.floor(start*1e6)/1e6:.6f}", "-i", str(span.path), "-map", "0:a:0",
                        "-vn", "-af", filt, "-ac", str(CHANNELS), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"]
                received = 0
                def consume(block):
                    nonlocal received
                    received += len(block)
                    if received > expected: raise TelemetryError("Prepared sound exceeds its planned duration")
                    wav.writeframesraw(block)
                _process(args, destination.with_suffix(".audio.log"), cancel, consume)
                if received != expected: raise TelemetryError("Prepared sound is incomplete")
            written += span.samples; progress(written/plan.samples)
    if written != plan.samples: raise TelemetryError("Prepared sound has gaps or overlapping segments")
    for path, expected in plan.sources.items():
        if stamp(path) != expected: raise TelemetryError(f"Sound source changed: {path.name}")


def attach_audio(video_path, sound_path, duration, cancel):
    """Mux after video encoding so -frames:v cannot truncate the sound tail."""
    target = video_path.with_name(video_path.stem + ".with-sound.mp4")
    if shutil.disk_usage(video_path.parent).free < video_path.stat().st_size + 64*1024*1024:
        raise TelemetryError("More free space is needed to attach the aligned sound track")
    args = [executable("ffmpeg"), "-v", "error", "-nostdin", "-n", "-i", str(video_path), "-i", str(sound_path),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.9f}", "-map_metadata", "0", "-movflags", "+faststart", str(target)]
    _process(args, video_path.with_suffix(".audio-mux.log"), cancel)
    meta = probe(target)
    audio = next((s for s in meta["streams"] if s["codec_type"] == "audio"), None)
    if (audio is None or abs(float(audio.get("start_time", 0))) > 1/RATE
            or abs(float(audio.get("duration", 0))-duration) > .002
            or int(audio.get("sample_rate", 0)) != RATE):
        raise TelemetryError("Output sound failed its timing verification")
    if cancel.is_set(): raise InterruptedError("Sound preparation cancelled")
    os.replace(target, video_path)
    sound_path.unlink()
    return {"source": "vbox_preferred", "codec": audio["codec_name"],
            "sample_rate": RATE, "channels": audio["channels"],
            "start_seconds": float(audio.get("start_time", 0)), "duration_seconds": float(audio["duration"])}
