"""Plan frame-aligned exports without changing source GPS or telemetry history."""
from dataclasses import dataclass, replace
from fractions import Fraction
from bisect import bisect_left, bisect_right
from statistics import median
import math

from .media import Video


@dataclass
class Part:
    source: Video
    video: Video
    start: float
    frames: int | None

    @property
    def end(self):
        return self.start + self.video.duration

    def contains(self, utc):
        seconds = self.source.clock.media_time(utc)
        return self.start - 1e-6 <= seconds < self.end

    def summary(self):
        return {"source_start_seconds": self.start, "source_end_seconds": self.end,
                "duration_seconds": self.video.duration, "frames": self.frames}


def video_parts(video: Video, matches, overlap_only=True) -> list[Part]:
    if not overlap_only:
        return [Part(video, video, 0, None)]
    fps = Fraction(video.fps)
    total_frames = math.ceil(video.duration * fps - 1e-5)
    spans, histories = [], {}
    for match in matches:
        if match.video is not video or not match.rows:
            continue
        start = max(0, video.clock.media_time(match.rows[0].utc))
        end = min(video.duration, video.clock.media_time(match.rows[-1].utc))
        # A recording chapter can end between telemetry samples. Retain its
        # boundary frames when the same continuous VBOX run spans that boundary.
        key = match.vbo.path
        if key not in histories:
            times = [row.utc for row in match.vbo.rows]
            gap = max(.5, 10 * median(b - a for a, b in zip(times, times[1:]))) if len(times) > 1 else .5
            histories[key] = times, gap
        times, gap = histories[key]
        before = bisect_left(times, match.rows[0].utc) - 1
        after = bisect_right(times, match.rows[-1].utc)
        if before >= 0 and times[before] <= video.clock.utc(0) and match.rows[0].utc - times[before] <= gap:
            start = 0
        if after < len(times) and times[after] >= video.clock.utc(video.duration) and times[after] - match.rows[-1].utc <= gap:
            end = video.duration
        # Keep the frames containing the first and last data samples. The half
        # millisecond also leaves room for VBO's millisecond video-time rounding.
        first = max(0, math.floor(start * fps + 1e-5))
        stop = min(total_frames, math.floor((end + .0005) * fps + 1e-5) + 1)
        if stop > first:
            spans.append((first, stop))
    merged = []
    for first, stop in sorted(spans):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
        else:
            merged.append((first, stop))
    parts = []
    for first, stop in merged:
        start = float(first / fps)
        clock = replace(video.clock, origin=video.clock.utc(start),
                        first_fix=video.clock.first_fix - start, last_fix=video.clock.last_fix - start)
        view = replace(video, duration=float((stop - first) / fps), clock=clock)
        parts.append(Part(video, view, start, stop - first))
    return parts
