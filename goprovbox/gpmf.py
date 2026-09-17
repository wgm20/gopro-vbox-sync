"""Bounds-checked reader for GoPro's documented, big-endian GPMF records.

No video decoding or third-party telemetry service is used. Unknown records
remain opaque. GPS9's TYPE descriptor is honoured (including mixed integers).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import struct


class TelemetryError(ValueError):
    pass


@dataclass(frozen=True)
class Record:
    key: str
    kind: str
    size: int
    count: int
    data: bytes

    def children(self) -> list[Record]:
        return records(self.data) if self.kind == "\0" else []

    def text(self) -> str:
        return self.data.decode("ascii", errors="replace").rstrip("\0")

    def values(self, descriptor: str | None = None) -> list[tuple]:
        formats = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "i",
                   "L": "I", "j": "q", "J": "Q", "f": "f", "d": "d", "q": "i", "Q": "q"}
        kinds = self.kind
        if kinds == "?":
            if not descriptor:
                raise TelemetryError(f"{self.key}: complex record lacks TYPE")
            kinds = re.sub(r"(.)\[(\d+)\]", lambda m: m[1] * int(m[2]), descriptor)
        try:
            fmt = ">" + "".join(formats[k] for k in kinds)
        except KeyError as exc:
            raise TelemetryError(f"{self.key}: unsupported numeric type {kinds!r}") from exc
        unit = struct.calcsize(fmt)
        if self.size % unit:
            raise TelemetryError(f"{self.key}: record size {self.size} does not fit {kinds}")
        fmt = ">" + fmt[1:] * (self.size // unit)
        return list(struct.iter_unpack(fmt, self.data))


def records(data: bytes, *, _depth: int = 0) -> list[Record]:
    if _depth > 16:
        raise TelemetryError("GPMF nesting is too deep")
    result = []
    pos = 0
    while pos < len(data):
        if not any(data[pos:pos + 8]):
            if any(data[pos:]):
                raise TelemetryError("Nonzero bytes after GPMF padding")
            break
        if len(data) - pos < 8:
            raise TelemetryError("Truncated GPMF header")
        key, kind, size, count = struct.unpack_from(">4sBBH", data, pos)
        if not all(32 <= b <= 126 for b in key):
            raise TelemetryError("Invalid GPMF key")
        length = size * count
        end = pos + 8 + length
        padded = pos + 8 + ((length + 3) & ~3)
        if padded > len(data):
            raise TelemetryError(f"Truncated GPMF payload {key!r}")
        record = Record(key.decode("ascii"), chr(kind), size, count, data[pos + 8:end])
        if kind == 0:
            records(record.data, _depth=_depth + 1)
        result.append(record)
        pos = padded
    return result


def streams(data: bytes):
    for record in records(data):
        if record.key == "STRM":
            yield record.children()
        elif record.kind == "\0":
            yield from streams(record.data)


def scaled(record: Record, siblings: dict[str, Record]) -> list[tuple[float, ...]]:
    descriptor = siblings["TYPE"].text() if "TYPE" in siblings else None
    values = record.values(descriptor)
    scale_record = siblings.get("SCAL")
    scales = [v for row in scale_record.values() for v in row] if scale_record else [1]
    result = []
    for row in values:
        if len(scales) not in (1, len(row)) or any(s == 0 for s in scales):
            raise TelemetryError(f"{record.key}: invalid SCAL")
        result.append(tuple(v / scales[i % len(scales)] for i, v in enumerate(row)))
    return result
