"""Data-only overlays, using logged (already calibrated) VBOX values.

VVHSN is read with the user's installed Racelogic library. No vendor binaries,
keys, fonts or artwork are redistributed. Archive members remain in memory.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import hashlib
import io
import math
import os
import re
import subprocess
import tarfile
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from .gpmf import TelemetryError
from .media import CREATE_NO_WINDOW

CHANNELS = {"speed": ("velocity",), "rpm": ("rpm", "engine_speed"),
            "throttle": ("tps", "throttle"), "brake": ("brake", "brake_pressure")}
MAX_ARCHIVE = 32 * 1024 * 1024


def numbers(text):
    result = tuple(float(x.strip()) for x in text.strip("() ").split(","))
    if not all(math.isfinite(x) for x in result):
        raise TelemetryError("Scene contains a non-finite number")
    return result


def datasource(text):
    parts = text.split()
    if not parts:
        return "", 1.0
    scale = 1.0
    for part in parts[1:]:
        if not part.startswith("scaling="):
            raise TelemetryError("Unsupported scene datasource expression")
        scale = float(part.split("=", 1)[1])
    if not math.isfinite(scale):
        raise TelemetryError("Invalid scene channel scaling")
    return parts[0], scale


def read_scene_archive(path: Path) -> dict[str, bytes]:
    if path.stat().st_size > MAX_ARCHIVE:
        raise TelemetryError("Scene file exceeds the 32 MB limit")
    if path.suffix.lower() != ".vvhsn":
        raise TelemetryError("Choose a VBOX HD2 .VVHSN scene file")
    if os.name != "nt":
        raise TelemetryError("Reading VVHSN scenes requires VBOX Video Setup on Windows")
    library = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Racelogic/VBOX Video/Racelogic.Core.dll"
    if not library.is_file():
        raise TelemetryError("Install VBOX Video Setup to use a VVHSN scene, or leave Scene blank for the built-in dashboard")
    # Values travel in the environment, never in executable PowerShell source.
    script = "$ErrorActionPreference='Stop'; $a=[Reflection.Assembly]::LoadFrom($env:GVS_SCENE_LIBRARY); $r=[Activator]::CreateInstance($a.GetType('Racelogic.Core.TripleDESEncryption')); $r.Decrypt($env:GVS_SCENE_SOURCE,$env:GVS_SCENE_DEST)"
    with TemporaryDirectory(prefix="gvs-scene-") as tmp:
        dest = Path(tmp) / "scene.tar"
        env = os.environ | {"GVS_SCENE_LIBRARY": str(library), "GVS_SCENE_SOURCE": str(path.resolve()), "GVS_SCENE_DEST": str(dest)}
        try:
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                  env=env, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            if proc.returncode or not dest.is_file():
                raise TelemetryError("VBOX Video Setup could not read this scene. Try opening it in the scene editor first.")
            if dest.stat().st_size > MAX_ARCHIVE:
                raise TelemetryError("Expanded scene exceeds the 32 MB limit")
            return archive_members(dest.read_bytes())
        except subprocess.TimeoutExpired as exc:
            raise TelemetryError("Reading the VBOX scene timed out") from exc


def archive_members(data: bytes) -> dict[str, bytes]:
    result, total = {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive:
                name = member.name.replace("\\", "/")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or ":" in name or member.issym() or member.islnk():
                    raise TelemetryError("Unsafe member in scene archive")
                if member.isdir():
                    continue
                if not member.isfile() or name in result:
                    raise TelemetryError("Invalid or duplicate scene archive member")
                total += member.size
                if total > MAX_ARCHIVE or len(result) >= 512:
                    raise TelemetryError("Scene archive exceeds size limits")
                result[name] = archive.extractfile(member).read()
    except tarfile.TarError as exc:
        raise TelemetryError("Invalid scene archive") from exc
    return result


def scene_xml(raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise TelemetryError("Scene XML declarations are unsupported")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise TelemetryError("Invalid scene XML") from exc


@dataclass
class Scene:
    name: str
    digest: str
    size: tuple[int, int]
    elements: list
    assets: dict
    fonts: dict
    omitted: list[str]
    bindings: dict
    mode: str = "four"
    options: dict = field(default_factory=dict)

    def summary(self):
        return {"name": self.name, "sha256": self.digest, "channels": self.bindings,
                "mode": self.mode, "omitted_elements": self.omitted,
                "placement": "Uniform scale with complete widgets inside the frame; map uses equal ground-distance scales",
                "notes": self.options.get("notes", [])}


def load_scene(path: Path, mode="four") -> Scene:
    if path.stat().st_size > MAX_ARCHIVE:
        raise TelemetryError("Scene file exceeds the 32 MB limit")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    members = read_scene_archive(path)
    if mode == "full":
        from .fullscene import parse_full_scene
        scene = parse_full_scene(members, path.name, before)
    elif mode == "four":
        scene = parse_scene(members, path.name, before)
    else:
        raise TelemetryError("Unknown scene mode")
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise TelemetryError("Scene changed while being read; select it again")
    return scene


def parse_scene(members, name="Scene", digest="") -> Scene:
    """Supported subset: the four requested channels as text and bars + artwork.

    Other widgets are explicitly reported. No camera region is ever imported.
    """
    try:
        scene = scene_xml(members["SCENE.XML"]).find(".//scene")
        config = scene_xml(members["VBOXHD.XML"])
        layout = scene.find("layouts/layout")
        size = tuple(int(v) for v in numbers(layout.get("format")))
        if len(size) != 2 or not all(16 <= v <= 8192 for v in size):
            raise TelemetryError("Unsupported scene canvas size")
        assets, fonts, bindings, elements, omitted = {}, {}, {}, [], []
        def asset(path):
            if path not in assets:
                im = Image.open(io.BytesIO(members[path]))
                if im.format != "PNG" or im.width * im.height > 16_000_000:
                    raise TelemetryError("Unsupported or oversized scene artwork")
                if sum(a.width*a.height for a in assets.values()) + im.width*im.height > 32_000_000:
                    raise TelemetryError("Decoded scene artwork exceeds the size limit")
                assets[path] = im.convert("RGBA")
            return assets[path]
        image_paths = {e.get("name"): e.get("path") for e in scene.findall("images/image")}
        for f in scene.findall("fonts/font"):
            sheet = asset(f.get("glyphs"))
            offsets = [(int(c, 16), int(x)) for c, x in re.findall(r"\(0x([0-9a-fA-F]+),(\d+)\)", f.text or "")]
            if not offsets or len(offsets) > 512 or offsets[0][1] < 0 or offsets[-1][1] >= sheet.width or any(a[1] >= b[1] for a, b in zip(offsets, offsets[1:])):
                raise TelemetryError("Invalid scene font offsets")
            fonts[f.get("name")] = {chr(c): sheet.crop((x, 0, offsets[i+1][1] if i+1 < len(offsets) else sheet.width, sheet.height))
                                     for i, (c, x) in enumerate(offsets)}
        units = {e.get("name", "").lower(): e.get("units", "") for e in config.findall(".//channels/channel")}
        graphics = {e.get("name"): e for e in scene.find("graphics")}
        statics = []
        for item in sorted(layout.findall("graphic"), key=lambda e: int(e.get("zorder", 0))):
            e = graphics[item.get("name")]
            pos = tuple(int(v) for v in numbers(item.get("screen_pos")))
            if len(pos) != 2 or any(abs(v) > 16384 for v in pos):
                raise TelemetryError("Invalid scene widget position")
            if e.tag == "static_image":
                path = image_paths[e.get("background")]
                im = asset(path)
                statics.append((e, pos, im))
                continue
            source, scale = datasource(e.get("datasource", ""))
            can = re.fullmatch(r"can\.bin\[\d+\]\.channel\(([^)]+)\)", source)
            column = can[1].lower() if can else "velocity" if source == "vbox.speed_gnd_mps" else ""
            key = next((k for k, aliases in CHANNELS.items() if column in aliases), None)
            if key is None:
                omitted.append(e.get("name", e.tag)); continue
            if e.tag not in ("text", "multibar"):
                raise TelemetryError(f"{key}: this scene's {e.tag} widget is not supported yet; use the built-in dashboard")
            binding = {"column": column, "factor": scale / 3.6 if key == "speed" else scale,
                       "unit": ("mph" if abs(scale - 2.236936292054402) < 1e-7 else "km/h" if abs(scale - 3.6) < 1e-7 else "m/s") if key == "speed" else units.get(column, "")}
            if key == "speed" and not any(abs(scale-s) < 1e-7 for s in (1, 3.6, 2.236936292054402)):
                raise TelemetryError("Unsupported speed display scale")
            if key in bindings and bindings[key] != binding:
                raise TelemetryError(f"Scene has conflicting {key} display scales")
            bindings[key] = binding
            background = asset(image_paths[e.get("background")]) if e.get("background") else None
            if e.tag == "text":
                font = fonts[e.get("font")]
                box = numbers(e.get("offset"))
                if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
                    raise TelemetryError("Invalid text widget dimensions")
                # A small, strictly bounded subset of printf. No evaluation.
                fmt = e.get("fmtstr", "%d")
                if not re.fullmatch(r"%[+ 0]?\d{0,2}(?:\.\d{1,2})?(?:l|ll|h)?[diuf]", fmt):
                    raise TelemetryError("Unsupported scene number format")
                element = {"kind": "text", "key": key, "pos": pos, "size": (int(box[2]), int(box[3])),
                           "box": box, "font": font, "fmt": re.sub(r"[lh]", "", fmt).replace("u", "d"),
                           "justification": e.get("justification", "middle_right")}
            else:
                bars = []
                for b in e.findall("graphics_bar"):
                    lo, hi = numbers(b.get("range")); bw, bh = numbers(b.get("size")); bx, by = numbers(b.get("offset"))
                    colour = numbers(b.get("colour"))
                    if hi <= lo or min(bw, bh) <= 0 or len(colour) != 4 or b.get("barmode") not in ("bottom_top_min", "left_right_min"):
                        raise TelemetryError("Unsupported scene bar geometry")
                    bars.append((lo, hi, int(bw), int(bh), int(bx), int(by), tuple(int(v) for v in colour[1:] + colour[:1]), b.get("barmode")))
                if not bars or background is None:
                    raise TelemetryError("Scene bar requires artwork and a valid range")
                element = {"kind": "bar", "key": key, "pos": pos, "size": background.size, "bars": bars, "image": background}
            element["name"] = e.get("name")
            elements.append(element)
        if set(bindings) != set(CHANNELS):
            raise TelemetryError("Scene must contain speed, RPM, throttle and brake text/bar widgets; use the built-in dashboard otherwise")
        # Include backing panels that overlap a selected widget, and nearby labels
        # that explicitly name a selected channel. Exclude every PiP border.
        selected_art = []
        for e, pos, im in statics:
            x, y = pos; label = e.find("label")
            relevant = label is not None and (label.get("text", "").lower() in ("rpm", "mph", "km/h", "throttle", "brake", "psi", "%"))
            overlap = label is None and any(x <= p["pos"][0] < x+im.width and y <= p["pos"][1] < y+im.height for p in elements)
            if (relevant or overlap) and "pip" not in e.get("name", "").lower():
                selected_art.append({"kind": "image", "pos": pos, "size": im.size, "image": im})
            else:
                omitted.append(e.get("name", "image"))
        elements = sorted(selected_art, key=lambda e: -e["size"][0]*e["size"][1]) + elements
        omitted.extend(e.get("name", "camera") for e in scene.findall("video_mappings/video_mapping"))
        return Scene(name, digest, size, elements, assets, fonts, omitted, bindings)
    except (KeyError, AttributeError, TypeError, ValueError, IndexError, OSError) as exc:
        raise TelemetryError(f"Cannot read this scene's four-channel overlay: {exc}") from exc


class Timeline:
    def __init__(self, matches, bindings):
        self.segments = []
        for match in sorted(matches, key=lambda m: m.rows[0].utc):
            vbo = match.vbo
            indices = {key: vbo.index(binding["column"]) for key, binding in bindings.items()}
            # The velocity column in the documented VBO format is kilometres/hour.
            if "velocity kmh" not in [p.strip().lower() for p in vbo.preamble]:
                raise TelemetryError(f"{vbo.path.name}: overlay needs velocity kmh")
            standard = {"sats", "time", "lat", "long", "velocity", "heading", "height", "vert-vel", "tsample",
                        "solution_type", "avifileindex", "avitime", "avisynctime"}
            custom = [c for c in vbo.columns if c not in standard]
            marker = next((i for i, line in enumerate(vbo.preamble) if line.lower().strip() == "[channel units]"), None)
            unit_lines = []
            if marker is not None:
                for line in vbo.preamble[marker+1:]:
                    if line.startswith("["): break
                    if line.strip(): unit_lines.append(line.strip().lower())
            # Racelogic omits built-in channel units, except sampleperiod. The
            # custom units follow in the same order as the custom columns.
            known_units = dict(zip(custom, unit_lines[-len(custom):])) if custom and len(unit_lines) >= len(custom) else {}
            for key in ("rpm", "throttle", "brake"):
                expected = bindings[key]["unit"].lower()
                actual = known_units.get(bindings[key]["column"], "")
                if not expected or actual != expected:
                    raise TelemetryError(f"{vbo.path.name}: {key} unit is {actual or 'unknown'}, scene expects {expected or 'unknown'}")
            times = [r.utc for r in match.rows]
            if self.segments and times[0] < self.segments[-1][0][-1]:
                raise TelemetryError("Overlapping VBOX files give ambiguous overlay data for this video")
            values = [{k: float(r.values[i]) * bindings[k]["factor"] for k, i in indices.items()} for r in match.rows]
            self.segments.append((times, values))

    def at(self, utc):
        for times, values in self.segments:
            if utc < times[0] or utc > times[-1]:
                continue
            j = bisect_left(times, utc)
            if j < len(times) and abs(times[j] - utc) < 1e-6:
                return values[j]
            if j == 0 or j == len(times) or times[j] - times[j-1] > .5:
                return None
            f = (utc - times[j-1]) / (times[j] - times[j-1])
            return {k: values[j-1][k] * (1-f) + values[j][k] * f for k in values[j]}
        return None


class FourChannelRenderer:
    def __init__(self, video, matches, width, height, scene=None):
        self.video, self.scene = video, scene
        self.bindings = scene.bindings if scene else {k: {"column": names[0], "factor": 1/1.609344 if k == "speed" else 1,
                                                       "unit": {"speed":"mph", "rpm":"rpm", "throttle":"%", "brake":"psi"}[k]} for k, names in CHANNELS.items()}
        self.timeline = Timeline(matches, self.bindings)
        if scene:
            self.scale = min(width / scene.size[0], height / scene.size[1])
            left = min(e["pos"][0] for e in scene.elements); top = min(e["pos"][1] for e in scene.elements)
            right = max(e["pos"][0]+e["size"][0] for e in scene.elements)
            bottom = max(e["pos"][1]+e["size"][1] for e in scene.elements)
            if left < 0 or top < 0 or right > scene.size[0] or bottom > scene.size[1]:
                raise TelemetryError("Selected scene gauges extend outside its canvas")
            self.origin = (left, top)
            self.native_size = (right-left, bottom-top + 24)
            self.position = (round(left*self.scale), round(top*self.scale))
        else:
            self.scale = min(width / 1920, height / 1080)
            self.origin = (0, 0); self.native_size = (1080, 160)
            self.position = (round(24*self.scale), height - round(184*self.scale))
        self.size = tuple(max(1, round(v*self.scale)) for v in self.native_size)
        if self.position[0] + self.size[0] > width or self.position[1] + self.size[1] > height:
            raise TelemetryError("Overlay does not fit this video frame")
        self.blank = Image.new("RGBA", self.size)
        fontpath = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeuib.ttf"
        self.fonts = {s: ImageFont.truetype(str(fontpath), s) if fontpath.exists() else ImageFont.load_default(size=s) for s in (14, 22, 48)}

    def frame(self, seconds):
        values = self.timeline.at(self.video.clock.utc(seconds))
        if values is None:
            return self.blank
        im = Image.new("RGBA", self.native_size)
        draw = ImageDraw.Draw(im)
        if self.scene:
            for e in self.scene.elements:
                x, y = (e["pos"][i]-self.origin[i] for i in range(2))
                if e["kind"] in ("image", "bar"):
                    im.alpha_composite(e["image"], (x, y))
                if e["kind"] == "bar":
                    for lo, hi, w, h, bx, by, colour, mode in e["bars"]:
                        f = max(0, min(1, (values[e["key"]]-lo)/(hi-lo)))
                        n = round((h if mode == "bottom_top_min" else w)*f)
                        if n:
                            draw.rectangle((x+bx, y+by+h-n, x+bx+w-1, y+by+h-1) if mode == "bottom_top_min" else (x+bx, y+by, x+bx+n-1, y+by+h-1), fill=colour)
                elif e["kind"] == "text":
                    text = e["fmt"] % values[e["key"]]
                    glyphs = [e["font"].get(c, e["font"].get("-")) for c in text]
                    tw = sum(g.width for g in glyphs); th = max(g.height for g in glyphs)
                    box = Image.new("RGBA", e["size"])
                    tx = max(0, (e["size"][0]-tw) if "right" in e["justification"] else (e["size"][0]-tw)//2 if "centre" in e["justification"] else 0)
                    ty = max(0, (e["size"][1]-th)//2)
                    for glyph in glyphs:
                        box.alpha_composite(glyph, (tx, ty)); tx += glyph.width
                    im.alpha_composite(box, (x+int(e["box"][0]), y+int(e["box"][1])))
            label = f"Speed {self.bindings['speed']['unit']}   RPM   Throttle {self.bindings['throttle']['unit']}   Brake {self.bindings['brake']['unit']}"
            draw.rectangle((0, self.native_size[1]-24, self.native_size[0], self.native_size[1]), fill=(8,20,30,210))
            draw.text((6, self.native_size[1]-22), label, font=self.fonts[14], fill="white")
        else:
            draw.rounded_rectangle((0,0,1079,159), radius=18, fill=(10,23,36,225))
            for i, (key, title, colour) in enumerate((("speed","SPEED · mph","#e4edf4"),("rpm","ENGINE · rpm","#57bbf0"),("throttle","THROTTLE · %","#35db94"),("brake","BRAKE · psi","#ff626b"))):
                x=24+i*270
                draw.text((x,15),title,font=self.fonts[22],fill=colour)
                draw.text((x,45),str(round(values[key])),font=self.fonts[48],fill="white")
                maximum={"speed":150,"rpm":10000,"throttle":100,"brake":600}[key]
                draw.rounded_rectangle((x,126,x+225,136),radius=5,fill=(68,82,96,255))
                length=round(225*max(0,min(1,values[key]/maximum)))
                if length: draw.rectangle((x,126,x+length,136),fill=colour)
        return im.resize(self.size, Image.Resampling.LANCZOS) if im.size != self.size else im

    def feed(self, pipe, cancel, errors):
        try:
            fps = Fraction(self.video.fps)
            for n in range(math.ceil(self.video.duration * fps)):
                if cancel.is_set():
                    break
                pipe.write(self.frame(float(n / fps)).tobytes())
        except (BrokenPipeError, OSError, ValueError) as exc:
            if not cancel.is_set(): errors.append(exc)
        except Exception as exc:
            errors.append(exc)
        finally:
            try: pipe.close()
            except OSError: pass


def Renderer(video, matches, width, height, scene=None):
    if scene is not None and scene.mode == "full":
        from .fullscene import FullSceneRenderer
        return FullSceneRenderer(video, matches, width, height, scene)
    return FourChannelRenderer(video, matches, width, height, scene)
