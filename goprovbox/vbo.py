"""Lossless channel preservation for Racelogic's space-delimited VBO format."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import math
import re

from .gpmf import TelemetryError


@dataclass
class Row:
    utc: float
    values: list[str]


@dataclass
class VBO:
    path: Path
    preamble: list[str]
    columns: list[str]
    rows: list[Row]
    encoding: str
    warnings: list[str]

    def index(self, *names: str) -> int:
        for name in names:
            if name.lower() in self.columns:
                return self.columns.index(name.lower())
        raise TelemetryError(f"{self.path.name}: required channel missing: {names[0]}")


def time_of_day(value: str) -> float:
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})(\.\d+)?", value)
    if not match:
        raise TelemetryError(f"Invalid VBOX time: {value!r}")
    h, m, s = map(int, match.group(1, 2, 3))
    if h > 23 or m > 59 or s > 59:
        raise TelemetryError(f"Invalid VBOX time: {value!r}")
    return h * 3600 + m * 60 + s + float(match[4] or 0)


def read_vbo(path: Path) -> VBO:
    raw = path.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "cp1252"
    lines = raw.decode(encoding).splitlines()
    markers = {line.strip().lower(): i for i, line in enumerate(lines) if line.strip().startswith("[")}
    for name in ("[header]", "[column names]", "[data]", "[avi]"):
        if name not in markers:
            raise TelemetryError(f"{path.name}: missing {name} section")
    date_match = re.search(r"File created on (\d{2}/\d{2}/\d{4})", lines[0])
    if not date_match:
        raise TelemetryError(f"{path.name}: missing/unrecognised UTC creation date")
    try:
        day = datetime.strptime(date_match[1], "%d/%m/%Y").replace(tzinfo=timezone.utc).timestamp()
    except ValueError as exc:
        raise TelemetryError(f"{path.name}: invalid creation date") from exc
    cols = " ".join(lines[markers["[column names]"] + 1:markers["[data]"]]).lower().split()
    if len(cols) != len(set(cols)):
        raise TelemetryError(f"{path.name}: duplicate column names")
    header_end = next((i for i in range(markers["[header]"] + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    headers = [x for x in lines[markers["[header]"] + 1:header_end] if x.strip()]
    if len(headers) != len(cols):
        raise TelemetryError(f"{path.name}: header/channel count mismatch")
    vbo = VBO(path, lines[:markers["[data]"] + 1], cols, [], encoding, [])
    time_idx = vbo.index("time")
    vbo.index("avifileindex"); vbo.index("avitime", "avisynctime")
    previous = None
    duplicates = 0
    for lineno, line in enumerate(lines[markers["[data]"] + 1:], markers["[data]"] + 2):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != len(cols):
            raise TelemetryError(f"{path.name}:{lineno}: expected {len(cols)} columns, found {len(fields)}")
        try:
            if not all(math.isfinite(float(x)) for x in fields):
                raise ValueError("non-finite value")
        except ValueError as exc:
            raise TelemetryError(f"{path.name}:{lineno}: invalid numeric sample") from exc
        tod = time_of_day(fields[time_idx])
        if previous is not None and tod < previous - 43200:
            day += 86400
        utc = day + tod
        if vbo.rows and utc < vbo.rows[-1].utc:
            raise TelemetryError(f"{path.name}:{lineno}: time goes backwards")
        if vbo.rows and utc == vbo.rows[-1].utc:
            if fields == vbo.rows[-1].values:
                duplicates += 1
                continue
            raise TelemetryError(f"{path.name}:{lineno}: conflicting data at the same timestamp")
        vbo.rows.append(Row(utc, fields))
        previous = tod
    if len(vbo.rows) < 2:
        raise TelemetryError(f"{path.name}: fewer than two telemetry samples")
    if duplicates:
        vbo.warnings.append(f"Removed {duplicates} exact duplicate samples.")
    return vbo


def write_vbo(vbo: VBO, rows: list[Row], video_times: list[float], prefix: str, destination: Path,
              video_indices: list[int] | None = None):
    if len(rows) != len(video_times) or not rows:
        raise TelemetryError("Invalid export sample mapping")
    preamble = vbo.preamble.copy()
    first = datetime.fromtimestamp(rows[0].utc, timezone.utc)
    preamble[0] = first.strftime("File created on %d/%m/%Y @ %H:%M:%S")
    avi = next(i for i, line in enumerate(preamble) if line.strip().lower() == "[avi]")
    end = next((i for i in range(avi + 1, len(preamble)) if preamble[i].startswith("[")), len(preamble))
    preamble[avi + 1:end] = [prefix, "mp4", ""]
    vi = vbo.index("avifileindex")
    ti = vbo.index("avitime", "avisynctime")
    video_indices = video_indices or [1] * len(rows)
    if len(video_indices) != len(rows):
        raise TelemetryError("Invalid video indices")
    last, last_index = -1, 0
    with destination.open("w", encoding=vbo.encoding, newline="") as file:
        file.write("\r\n".join(preamble) + "\r\n")
        for row, seconds, index in zip(rows, video_times, video_indices):
            millis = round(seconds * 1000)
            if millis < 0 or index < last_index or (index == last_index and millis < last):
                raise TelemetryError("Output video timestamps are negative or go backwards")
            fields = row.values.copy()
            fields[vi], fields[ti] = f"{index:04d}", f"{millis:09d}"
            file.write(" ".join(fields) + "\r\n")
            last, last_index = millis, index
