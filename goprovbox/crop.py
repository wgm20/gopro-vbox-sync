"""VBOX display proportions and one shared crop rectangle for preview/export."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
import math

from .gpmf import TelemetryError
from .media import probe

MODES = ("none", "top", "bottom", "centre")
Choice = str | dict[str, float | str]


def validate_choice(choice: Choice):
    if isinstance(choice, str) and choice in MODES:
        return
    if isinstance(choice, dict) and set(choice) == {"mode", "x", "y"} and choice["mode"] == "custom":
        if all(isinstance(choice[k], (int, float)) and not isinstance(choice[k], bool)
               and math.isfinite(choice[k]) and 0 <= choice[k] <= 1 for k in ("x", "y")):
            return
    raise TelemetryError("Invalid crop position; choose a position inside the video")


@dataclass(frozen=True)
class Crop:
    width: int
    height: int
    x: int
    y: int

    def filter(self):
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"

    def summary(self):
        return asdict(self)


@dataclass
class Reference:
    aspect: Fraction
    sources: dict[str, dict]

    @property
    def label(self):
        return f"{self.aspect.numerator}:{self.aspect.denominator}"

    def summary(self):
        return {"aspect": self.label, "sources": self.sources}


def rectangle(width: int, height: int, rotation: int, aspect: Fraction, mode: Choice) -> Crop | None:
    """Coordinates are in the upright frame. Even boundaries preserve 4:2:0 chroma."""
    validate_choice(mode)
    if mode == "none":
        return None
    if rotation not in (0, 90, 180, 270) or aspect <= 0 or min(width, height) < 2:
        raise TelemetryError("Invalid video dimensions, rotation or crop aspect ratio")
    w, h = (height, width) if rotation % 180 else (width, height)
    if Fraction(w, h) <= aspect:
        cw, ch = w, int(w / aspect)
    else:
        cw, ch = int(h * aspect), h
    cw, ch = cw // 2 * 2, ch // 2 * 2
    if min(cw, ch) < 2:
        raise TelemetryError("The VBOX aspect ratio leaves too little video to crop")
    # A wider source needs a centred side crop; vertical placement then has no effect.
    x = (w - cw) // 4 * 2
    y = ((h - ch) // 2 * 2 if mode == "top" else 0 if mode == "bottom" else (h - ch) // 4 * 2)
    if isinstance(mode, dict):
        # Fractions of the available travel keep framing independent of preview
        # size, output resolution and window size. Snap only at source pixels.
        x = round(mode["x"] * ((w - cw) // 2)) * 2
        y = round(mode["y"] * ((h - ch) // 2)) * 2
    return Crop(cw, ch, x, y)


def positioned(width: int, height: int, rotation: int, aspect: Fraction, x: float, y: float) -> dict:
    """Clamp a dragged top-left corner (upright source pixels) to a valid choice."""
    crop = rectangle(width, height, rotation, aspect, "centre")
    w, h = (height, width) if rotation % 180 else (width, height)
    def fraction(value, travel):
        if not math.isfinite(value):
            raise TelemetryError("Invalid crop position")
        return min(1., max(0., value / travel)) if travel else .5
    return {"mode": "custom", "x": fraction(x, (w - crop.width) // 2 * 2),
            "y": fraction(y, (h - crop.height) // 2 * 2)}


def display_aspect(metadata: dict) -> Fraction:
    stream = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "video"), None)
    if not stream:
        raise TelemetryError("Original VBOX file has no video stream")
    try:
        sar = stream.get("sample_aspect_ratio", "1:1")
        sar = Fraction(sar.replace(":", "/")) if sar not in (None, "N/A", "0:1") else Fraction(1)
        aspect = Fraction(int(stream["width"]), int(stream["height"])) * sar
        rotation = next((float(s["rotation"]) for s in stream.get("side_data_list", []) if "rotation" in s),
                        float(stream.get("tags", {}).get("rotate", 0)))
        if rotation % 90 or aspect <= 0:
            raise ValueError("invalid display shape")
        return 1 / aspect if rotation % 180 else aspect
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise TelemetryError("Cannot read the original VBOX video's display shape") from exc


def original_paths(matches) -> list[Path]:
    """Resolve only linked, overlapping chapters in the VBO's own folder."""
    paths = set()
    for match in matches:
        vbo = match.vbo
        start = next(i for i, line in enumerate(vbo.preamble) if line.strip().lower() == "[avi]") + 1
        values = []
        for line in vbo.preamble[start:]:
            if line.strip().startswith("["):
                break
            if line.strip():
                values.append(line.strip())
        if len(values) != 2:
            raise TelemetryError(f"{vbo.path.name}: cannot read its original video reference")
        prefix, extension = values
        extension = extension.lstrip(".")
        if any(c in prefix + extension for c in '/\\:') or extension.lower() not in ("mp4", "avi", "mov"):
            raise TelemetryError(f"{vbo.path.name}: original video must be in the recordings folder")
        index = vbo.index("avifileindex")
        indices = {int(float(row.values[index])) for row in match.rows if float(row.values[index]) > 0}
        files = {p.name.casefold(): p for p in vbo.path.parent.iterdir() if p.is_file()}
        for number in sorted(indices):
            name = f"{prefix}{number:04d}.{extension}"
            path = files.get(name.casefold())
            if path is None:
                raise TelemetryError(f"Keep {name} beside your VBOX runs to use VBOX cropping")
            paths.add(path)
    if not paths:
        raise TelemetryError("No original VBOX video is linked to this GoPro's matched data")
    return sorted(paths)


def reference_for(matches, cache=None) -> Reference:
    cache = {} if cache is None else cache
    sources, aspects = {}, set()
    for path in original_paths(matches):
        stat = path.stat()
        stamp = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        key = (path, stat.st_size, stat.st_mtime_ns)
        if key not in cache:
            cache[key] = display_aspect(probe(path))
        aspects.add(cache[key]); sources[path.name] = stamp
    if len(aspects) != 1:
        raise TelemetryError("The matched VBOX videos have different shapes; use No crop for this recording")
    return Reference(aspects.pop(), sources)
