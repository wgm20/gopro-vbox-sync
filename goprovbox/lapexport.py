"""Choose a complete recorded lap and cut it at the two line crossings."""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction
from statistics import median
import math
import re

from .media import Video
from .trimming import frame_part

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


@dataclass(frozen=True)
class Span:
    video: str
    start: float
    end: float


@dataclass(frozen=True)
class FilmedLap:
    run: str
    number: int
    start: float
    end: float
    coverage: tuple[Span, ...]

    @property
    def duration(self):
        return self.end - self.start

    @property
    def key(self):
        return self.run, self.number, self.spans()[0].video

    def spans(self, padding=0):
        start, end = self.start - padding, self.end + padding
        return [Span(s.video, max(start, s.start), min(end, s.end))
                for s in self.coverage if min(end, s.end) > max(start, s.start)]


def filmed_laps(scan, included):
    """Only join successive chapters of the same camera recording."""
    choices = []
    for vbo in scan.vbos:
        laps = scan.run_info.get(vbo.path.name, {}).get("laps", ())
        if not laps:
            continue
        times = [r.utc for r in vbo.rows]
        gap = max(.5, 10 * median(b - a for a, b in zip(times, times[1:])))
        matches = [m for m in scan.matches if m.vbo is vbo and m.video.path.name in included]
        videos = {m.video.path.name: m.video for m in matches}
        for group in recording_groups(list(videos.values())):
            blocks = []
            for video in group:
                lo, hi = video.clock.utc(0), video.clock.utc(video.duration)
                for m in sorted((m for m in matches if m.video is video and m.rows), key=lambda m: m.rows[0].utc):
                    start, end = m.rows[0].utc, m.rows[-1].utc
                    before, after = bisect_left(times, start)-1, bisect_right(times, end)
                    if before >= 0 and times[before] <= lo and start-times[before] <= gap:
                        start = lo
                    if after < len(times) and times[after] >= hi and times[after]-end <= gap:
                        end = hi
                    span = Span(video.path.name, max(lo, start), min(hi, end))
                    # GPS fits at a chapter seam may disagree by a fraction of a frame.
                    if blocks:
                        prev = blocks[-1][-1]
                        last = videos[prev.video]
                        if (last is not video and Fraction(last.fps) == Fraction(video.fps)
                                and abs(prev.end - span.start) <= .5 / float(Fraction(video.fps))
                                and abs(prev.end - last.clock.utc(last.duration)) < 1e-6
                                and abs(span.start - lo) < 1e-6):
                            blocks[-1].append(span)
                            continue
                    blocks.append([span])
            for block in blocks:
                for lap in laps:
                    start, end = lap["utc_start"], lap["utc_end"]
                    if start >= block[0].start - 1e-6 and end <= block[-1].end + 1e-6:
                        choices.append(FilmedLap(vbo.path.name, lap["number"], start, end, tuple(block)))
    return sorted(choices, key=lambda lap: (lap.duration, lap.start, lap.key))


def lap_part(video, span):
    fps = Fraction(video.fps)
    total = math.ceil(video.duration * fps - 1e-5)
    # Include the frame containing the entry crossing; stop at the next crossing.
    first = max(0, math.floor(video.clock.media_time(span.start) * fps + 1e-5))
    stop = min(total, math.ceil(video.clock.media_time(span.end) * fps - 1e-5))
    return frame_part(video, first, stop)
