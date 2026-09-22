"""Interactive framing using the exact source-pixel crop used by export."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .crop import Choice, positioned, rectangle
from .engine import dimensions
from .media import Video, preview


@dataclass
class FramingPreview:
    video: Video
    rotation: int
    aspect: Fraction | None
    source: Image.Image
    sizes: dict[bool, tuple[int, int]]
    overlays: dict[bool, Image.Image]
    seconds: float

    def crop(self, choice):
        return rectangle(self.video.width, self.video.height, self.rotation, self.aspect, choice)

    def output(self, choice, bounds):
        crop = self.crop(choice)
        enabled = crop is not None
        w, h = self.sizes[enabled]
        scale = min(1, bounds[0] / w, bounds[1] / h)
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        box = (crop.x, crop.y, crop.x + crop.width, crop.y + crop.height) if crop else None
        frame = self.source.resize(size, Image.Resampling.LANCZOS, box=box).convert("RGBA")
        if enabled in self.overlays:
            layer = self.overlays[enabled].resize(size, Image.Resampling.LANCZOS)
            frame.alpha_composite(layer)
        return frame


def prepare_preview(video, matches, rotation, max_size, aspect, mode, scene_path, destination, cancel):
    """Decode once; dragging reuses the source and export-sized gauge artwork."""
    from .overlay import load_scene, Renderer
    seconds = video.clock.media_time((matches[0].rows[0].utc + matches[0].rows[-1].utc) / 2) if matches else video.duration / 2
    seconds = max(0, min(seconds, max(0, video.duration - .1)))
    upright = (video.height, video.width) if rotation % 180 else (video.width, video.height)
    preview(video.path, seconds, rotation, destination, output_size=upright)
    with Image.open(destination) as frame:
        source = frame.convert("RGB")
    if cancel.is_set():
        raise InterruptedError("Cancelled")
    scene = load_scene(Path(scene_path), mode=mode) if scene_path else None
    sizes, overlays = {}, {}
    for enabled in ((False, True) if aspect else (False,)):
        crop = rectangle(video.width, video.height, rotation, aspect, "centre") if enabled else None
        w, h = sizes[enabled] = dimensions(video, rotation, max_size, crop)
        if mode != "none":
            renderer = Renderer(video, matches, w, h, scene)
            layer = Image.new("RGBA", (w, h))
            layer.alpha_composite(renderer.frame(seconds), renderer.position)
            overlays[enabled] = layer
        if cancel.is_set():
            raise InterruptedError("Cancelled")
    return FramingPreview(video, rotation, aspect, source, sizes, overlays, seconds)


class CropEditor(tk.Toplevel):
    def __init__(self, parent, prepared: FramingPreview, choice: Choice, apply, unavailable=""):
        super().__init__(parent)
        self.title("Frame & preview — " + prepared.video.path.name)
        self.transient(parent)
        width = min(1180, self.winfo_screenwidth() - 80)
        height = min(780, self.winfo_screenheight() - 100)
        x = max(0, min(self.winfo_screenwidth() - width - 20,
                       parent.winfo_rootx() + (parent.winfo_width() - width) // 2))
        y = max(0, min(self.winfo_screenheight() - height - 60,
                       parent.winfo_rooty() + (parent.winfo_height() - height) // 2))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(780, width), min(520, height))
        self.prepared, self.apply_callback = prepared, apply
        self.position = dict(choice) if isinstance(choice, dict) else choice if choice != "none" else "centre"
        self.enabled = tk.BooleanVar(self, value=choice != "none" and prepared.aspect is not None)
        self.pending = None
        self.drag = None
        self.input_image = self.output_image = None
        self.input_size = None
        self.input_box = None
        body = ttk.Frame(self, padding=18); body.pack(fill="both", expand=True)
        toolbar = ttk.Frame(body); toolbar.pack(fill="x", pady=(0, 12))
        label = f"Crop to VBOX {prepared.aspect.numerator}:{prepared.aspect.denominator}" if prepared.aspect else "Crop to VBOX shape"
        self.crop_toggle = ttk.Checkbutton(toolbar, text=label, variable=self.enabled, command=self.redraw)
        self.crop_toggle.pack(side="left")
        if prepared.aspect is None:
            self.crop_toggle.configure(state="disabled")
        self.centre_button = ttk.Button(toolbar, text="Centre crop", command=self.centre)
        self.centre_button.pack(side="right")
        panels = ttk.Frame(body); panels.pack(fill="both", expand=True)
        panels.columnconfigure(0, weight=1, uniform="preview")
        panels.columnconfigure(1, weight=1, uniform="preview")
        panels.rowconfigure(1, weight=1)
        ttk.Label(panels, text="Drag the box to frame your video", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.output_title = ttk.Label(panels, text="Output preview", font=("Segoe UI", 11, "bold"))
        self.output_title.grid(row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 8))
        self.input_canvas = tk.Canvas(panels, background="#172d42", highlightthickness=0, takefocus=True)
        self.input_canvas.grid(row=1, column=0, sticky="nsew")
        self.output_canvas = tk.Canvas(panels, background="#172d42", highlightthickness=0)
        self.output_canvas.grid(row=1, column=1, sticky="nsew", padx=(16, 0))
        for canvas in (self.input_canvas, self.output_canvas):
            canvas.bind("<Configure>", self.schedule_redraw)
        self.input_canvas.bind("<ButtonPress-1>", self.press)
        self.input_canvas.bind("<B1-Motion>", self.motion)
        self.input_canvas.bind("<ButtonRelease-1>", self.release)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            self.input_canvas.bind("<" + key + ">", lambda e, x=dx, y=dy: self.nudge(x, y))
            self.input_canvas.bind("<Shift-" + key + ">", lambda e, x=dx, y=dy: self.nudge(x * 10, y * 10))
        self.hint = ttk.Label(body, wraplength=1050)
        self.hint.pack(fill="x", pady=(10, 12))
        self.unavailable = unavailable or "Keep the original VBOX video beside the VBOX runs to enable cropping."
        actions = ttk.Frame(body); actions.pack(fill="x")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Apply", style="Accent.TButton", command=self.commit).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.schedule_redraw()

    def choice(self):
        if not self.enabled.get():
            return "none"
        crop = self.prepared.crop(self.position)
        return self.at(crop.x, crop.y)

    def at(self, x, y):
        p = self.prepared
        return positioned(p.video.width, p.video.height, p.rotation, p.aspect, x, y)

    def schedule_redraw(self, *_):
        if self.pending is None:
            self.pending = self.after(16, self.redraw)

    def redraw(self):
        if self.pending is not None:
            self.after_cancel(self.pending); self.pending = None
        p = self.prepared
        self.hint.configure(wraplength=max(100, self.winfo_width() - 40))
        self.centre_button.configure(state="normal" if self.enabled.get() else "disabled")
        cw, ch = self.input_canvas.winfo_width(), self.input_canvas.winfo_height()
        ow, oh = self.output_canvas.winfo_width(), self.output_canvas.winfo_height()
        if min(cw, ch, ow, oh) < 20:
            return
        if self.input_size != (cw, ch):
            frame = p.source.copy()
            frame.thumbnail((cw - 24, ch - 24), Image.Resampling.LANCZOS)
            self.input_image = ImageTk.PhotoImage(frame, master=self)
            self.input_size = (cw, ch)
        fw, fh = self.input_image.width(), self.input_image.height()
        x, y = (cw - fw) / 2, (ch - fh) / 2
        self.input_box = (x, y, fw / p.source.width, fh / p.source.height)
        canvas = self.input_canvas; canvas.delete("all")
        canvas.create_image(x, y, image=self.input_image, anchor="nw")
        crop = p.crop(self.choice())
        if crop:
            sx, sy = self.input_box[2:]
            l, t = x + crop.x * sx, y + crop.y * sy
            r, b = l + crop.width * sx, t + crop.height * sy
            right, bottom = x + fw, y + fh
            for box in ((x, y, right, t), (x, b, right, bottom), (x, t, l, b), (r, t, right, b)):
                canvas.create_rectangle(*box, fill="#000000", stipple="gray50", width=0)
            canvas.create_rectangle(l, t, r, b, outline="#4ef2cb", width=3)
            for fraction in (1 / 3, 2 / 3):
                canvas.create_line(l + (r-l)*fraction, t, l + (r-l)*fraction, b, fill="#e5fff9", dash=(3, 5))
                canvas.create_line(l, t + (b-t)*fraction, r, t + (b-t)*fraction, fill="#e5fff9", dash=(3, 5))
            movable = crop.width < p.source.width - 1 or crop.height < p.source.height - 1
            self.hint.configure(text="Drag the box. Arrow keys make small adjustments; Shift + arrows moves further." if movable else "This video already has the VBOX shape; the whole picture fits.")
            canvas.configure(cursor="fleur" if movable else "arrow")
        else:
            self.hint.configure(text="Tick Crop to VBOX to position the crop." if p.aspect else self.unavailable)
            canvas.configure(cursor="arrow")
        output = p.output(self.choice(), (ow - 24, oh - 24))
        self.output_image = ImageTk.PhotoImage(output, master=self)
        self.output_canvas.delete("all")
        self.output_canvas.create_image(ow / 2, oh / 2, image=self.output_image)
        w, h = p.sizes[crop is not None]
        self.output_title.configure(text=f"Output preview · {w} × {h}")

    def press(self, event):
        if not self.enabled.get() or self.input_box is None:
            return
        self.input_canvas.focus_set()
        x, y, sx, sy = self.input_box
        px, py = (event.x - x) / sx, (event.y - y) / sy
        crop = self.prepared.crop(self.choice())
        if crop.x <= px <= crop.x + crop.width and crop.y <= py <= crop.y + crop.height:
            self.drag = (px - crop.x, py - crop.y)

    def motion(self, event):
        if self.drag is None or not self.enabled.get():
            return
        x, y, sx, sy = self.input_box
        self.position = self.at((event.x - x) / sx - self.drag[0], (event.y - y) / sy - self.drag[1])
        self.schedule_redraw()

    def release(self, event):
        self.motion(event)
        self.drag = None

    def nudge(self, dx, dy):
        if self.enabled.get():
            crop = self.prepared.crop(self.choice())
            self.position = self.at(crop.x + dx * 2, crop.y + dy * 2)
            self.redraw()
        return "break"

    def centre(self):
        self.position = "centre"
        self.redraw()

    def commit(self):
        self.apply_callback(self.choice())
        self.destroy()

    def destroy(self):
        if self.pending is not None:
            self.after_cancel(self.pending); self.pending = None
        super().destroy()
