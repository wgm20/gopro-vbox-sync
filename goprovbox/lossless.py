"""Original-stream export planning and validation; never changes input media."""
from pathlib import Path
import math
import os
import re

from .gpmf import TelemetryError
from .media import probe


def container_rotation(metadata):
    stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    angle = next((float(s["rotation"]) for s in stream.get("side_data_list", []) if "rotation" in s),
                 -float(stream.get("tags", {}).get("rotate", 0)))
    if not math.isfinite(angle):
        return None
    clockwise = (-angle) % 360
    nearest = round(clockwise / 90) * 90 % 360
    return nearest if abs((clockwise - nearest + 180) % 360 - 180) < .1 else None


def original_reference(video, output, rotation, metadata):
    """Circuit Tools appends a four-digit AVI index to the prefix."""
    match = re.fullmatch(r"(.+?)(\d{4})", video.path.stem)
    if (video.path.suffix.lower() != ".mp4" or not match or int(match[2]) == 0
            or container_rotation(metadata) != rotation):
        return None
    prefix_path = video.path.parent / match[1]
    try:
        prefix = os.path.relpath(prefix_path, output)
    except ValueError:  # Different Windows drives require an absolute reference.
        prefix = str(prefix_path)
    try:
        prefix.encode("cp1252")
    except UnicodeEncodeError:
        return None  # Standard VBO headers need an encodable AVI prefix.
    return prefix, int(match[2])


def validate_original(video, destination, rotation, source_metadata):
    result = probe(destination)
    source = next(s for s in source_metadata["streams"] if s["codec_type"] == "video")
    target = next(s for s in result["streams"] if s["codec_type"] == "video")
    for key in ("codec_name", "profile", "width", "height", "pix_fmt", "nb_frames", "avg_frame_rate",
                "color_range", "color_space", "color_transfer", "color_primaries"):
        if source.get(key) != target.get(key):
            raise TelemetryError(f"Original-quality video changed {key}")
    for key in ("duration", "start_time"):
        if abs(float(source.get(key, 0)) - float(target.get(key, 0))) > .002:
            raise TelemetryError(f"Original-quality video changed {key}")
    if container_rotation(result) != rotation:
        raise TelemetryError("The original-quality video has incorrect rotation metadata")
    old_audio = [s for s in source_metadata["streams"] if s["codec_type"] == "audio"]
    new_audio = [s for s in result["streams"] if s["codec_type"] == "audio"]
    if len(old_audio) != len(new_audio):
        raise TelemetryError("Original-quality video lost an audio track")
    for old, new in zip(old_audio, new_audio):
        for key in ("codec_name", "sample_rate", "channels", "nb_frames"):
            if old.get(key) != new.get(key):
                raise TelemetryError(f"Original-quality audio changed {key}")
        for key in ("duration", "start_time"):
            if abs(float(old.get(key, 0)) - float(new.get(key, 0))) > .002:
                raise TelemetryError(f"Original-quality audio changed {key}")
    w, h = video.width, video.height
    if rotation % 180:
        w, h = h, w
    return {"codec": target["codec_name"], "dimensions": [w, h], "frames": target.get("nb_frames"),
            "duration": target["duration"], "size_bytes": destination.stat().st_size,
            "video_and_audio_reencoded": False}
