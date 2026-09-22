"""Threaded desktop interface; all Tk access stays on the UI thread."""
from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
import json
import os
import queue
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import traceback
import webbrowser
from PIL import Image, ImageTk

from . import __version__
from .engine import scan_folder, export
from .media import preview
from .crop import rectangle
from .distribution import (asset, data_directory, tools_ready, download_tools, copy_demo,
                           uninstall_command, DOWNLOAD_PAGE, RELEASES, REPOSITORY)

ROTATIONS = {"Upright — no rotation": 0, "90° clockwise": 90, "180°": 180, "90° anticlockwise": 270}
QUALITY = {"HD · 1920 px": 1920, "Full resolution": 0, "Compact · 1280 px": 1280}
OVERLAYS = {"None": "none", "Driving data": "four", "Full scene · no rear camera": "full"}
CROPS = {"No crop": "none", "VBOX shape · cut off top": "top", "VBOX shape · cut off bottom": "bottom",
         "VBOX shape · centre crop": "centre"}



def open_path(path: Path):
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if __import__("sys").platform == "darwin" else "xdg-open", str(path)])


class App:
    def __init__(self, root: tk.Tk, folder: Path | None = None):
        self.root = root
        self.events = queue.Queue()
        self.cancel = Event()
        self.scan = None
        self.result = None
        self.busy = False
        self.closing = False
        self.rotations = {}
        self.crops = {}
        self.included_videos = set()
        self.preview_generation = 0
        self.preview_image = None
        self.temporary = tempfile.TemporaryDirectory(prefix="goprovbox-preview-")
        self.preferences = data_directory() / "settings.json"
        try:
            prefs = json.loads(self.preferences.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prefs = {}
        self.welcomed = bool(prefs.get("welcomed", False))
        self.folder = tk.StringVar(value=str(folder or prefs.get("folder", "")))
        self.output = tk.StringVar()
        self.quality = tk.StringVar(value=prefs.get("quality", next(iter(QUALITY))))
        legacy = {"Full resolution · re-encode": "Full resolution", "Original resolution": "Full resolution",
                  "Compact · re-encode at 1280px": "Compact · 1280 px", "Compact · 1280px longest edge": "Compact · 1280 px"}
        self.quality.set(legacy.get(self.quality.get(), self.quality.get()))
        if self.quality.get() not in QUALITY:
            self.quality.set(next(iter(QUALITY)))
        self.rotation = tk.StringVar()
        self.crop_choice = tk.StringVar(value="No crop")
        self.crop_note = tk.StringVar()
        self.overlay_mode = tk.StringVar(value=prefs.get("overlay_mode", "Driving data" if prefs.get("overlay_enabled") else "None"))
        if self.overlay_mode.get() not in OVERLAYS: self.overlay_mode.set("None")
        self.scene_path = tk.StringVar(value=prefs.get("scene_path", ""))
        self.status = tk.StringVar(value="Choose a recordings folder to begin.")
        self.summary = tk.StringVar(value="")
        root.title(f"GoPro VBOX Sync {__version__}")
        root.geometry("1080x810"); root.minsize(880, 740)
        root.configure(bg="#f4f6f8")
        if asset("icon.png").is_file():
            self.app_icon = tk.PhotoImage(file=str(asset("icon.png")))
            root.iconphoto(True, self.app_icon)
        style = ttk.Style(root); style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f4f6f8", foreground="#203047")
        style.configure("TButton", padding=(12, 7))
        style.configure("Accent.TButton", background="#087f74", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#04675e"), ("disabled", "#b6c7c5")])
        style.configure("Treeview", rowheight=32, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=6)
        style.map("Treeview", background=[("selected", "#d1ebe5")], foreground=[("selected", "#15392f")])
        style.configure("TProgressbar", background="#078b7e", troughcolor="#dce3e9")
        header = tk.Frame(root, bg="#172d42", padx=22, pady=13); header.pack(fill="x")
        with Image.open(asset("brand.png")) as artwork:
            self.header_brand = ImageTk.PhotoImage(artwork.resize((44, 44), Image.Resampling.LANCZOS), master=root)
            self.welcome_brand = ImageTk.PhotoImage(artwork.resize((64, 64), Image.Resampling.LANCZOS), master=root)
        tk.Label(header, image=self.header_brand, bg="#172d42", borderwidth=0).pack(side="left", padx=(0,14))
        tk.Label(header, text="GoPro VBOX Sync", bg="#172d42", fg="white", font=("Segoe UI", 19, "bold")).pack(side="left")
        tk.Label(header, text=__version__, bg="#172d42", fg="#c1ced8", font=("Segoe UI", 10)).pack(side="right")
        help_button = ttk.Menubutton(header, text="Help")
        help_button.pack(side="right", padx=18)
        help_menu = tk.Menu(help_button, tearoff=False)
        help_menu.add_command(label="Quick-start guide", command=self.open_help)
        help_menu.add_command(label="Try practice recordings", command=self.try_demo)
        help_menu.add_command(label="Set up video tools", command=self.setup_tools)
        help_menu.add_separator()
        help_menu.add_command(label="Download page", command=self.open_download_page)
        help_menu.add_command(label="Check for updates", command=lambda: webbrowser.open(RELEASES))
        help_menu.add_command(label="Source code and support", command=lambda: webbrowser.open(REPOSITORY))
        help_menu.add_command(label="About and licences", command=self.about)
        help_menu.add_separator()
        help_menu.add_command(label="Uninstall app…", command=self.uninstall)
        self.help_menu = help_menu
        help_button.configure(menu=help_menu)
        main = ttk.Frame(root, padding=(20,16)); main.pack(fill="both", expand=True)
        line = ttk.Frame(main); line.pack(fill="x")
        ttk.Label(line, text="Folder", width=9).pack(side="left")
        self.source_entry = ttk.Entry(line, textvariable=self.folder); self.source_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.browse = ttk.Button(line, text="Browse…", command=self.choose_folder); self.browse.pack(side="left", padx=8)
        self.scan_button = ttk.Button(line, text="Scan", style="Accent.TButton", command=self.begin_scan); self.scan_button.pack(side="left")
        ttk.Label(main, textvariable=self.summary).pack(anchor="w", pady=(10,8))
        panes = ttk.Panedwindow(main, orient="horizontal"); panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes); right = ttk.Frame(panes, padding=(16,0,0,0))
        panes.add(left, weight=3); panes.add(right, weight=2)
        self.tree = ttk.Treeview(left, columns=("include", "video", "vbox", "overlap"), show="headings", height=5, selectmode="browse")
        self.tree.heading("include", text="Include")
        self.tree.column("include", width=68, minwidth=68, stretch=False, anchor="center")
        for key, text, width in [("video", "GoPro", 155), ("vbox", "VBOX", 145), ("overlap", "Overlap", 85)]:
            self.tree.heading(key, text=text); self.tree.column(key, width=width, minwidth=75)
        self.tree.pack(fill="both", expand=True); self.tree.bind("<<TreeviewSelect>>", self.select_video)
        self.tree.bind("<Button-1>", self.click_include)
        self.tree.bind("<space>", self.toggle_focused_include)
        self.log_messages = []
        ttk.Button(left, text="Details…", command=self.show_details).pack(anchor="w", pady=(8,0))
        self.preview_label = ttk.Label(right, text="Preview", anchor="center", background="#dce3e9")
        self.preview_label.pack(fill="both", expand=True)
        rotationrow = ttk.Frame(right); rotationrow.pack(fill="x", pady=(8,0))
        self.rotate_combo = ttk.Combobox(rotationrow, textvariable=self.rotation, values=list(ROTATIONS), state="disabled", width=22)
        self.rotate_combo.pack(side="left",fill="x",expand=True); self.rotate_combo.bind("<<ComboboxSelected>>", self.change_rotation)
        self.overlay_preview = ttk.Button(rotationrow, text="Preview output", command=self.preview_overlay, state="disabled")
        self.overlay_preview.pack(side="left",padx=(8,0))
        croprow = ttk.Frame(right); croprow.pack(fill="x", pady=(8,0))
        ttk.Label(croprow, text="Crop").pack(side="left", padx=(0,8))
        self.crop_combo = ttk.Combobox(croprow, textvariable=self.crop_choice, values=list(CROPS), state="disabled", width=29)
        self.crop_combo.pack(side="left", fill="x", expand=True)
        self.crop_combo.bind("<<ComboboxSelected>>", self.change_crop)
        ttk.Label(right, textvariable=self.crop_note, wraplength=360).pack(anchor="w", pady=(4,0))
        options = ttk.Frame(main); options.pack(fill="x", pady=(16,10))
        ttk.Label(options, text="Resolution", width=9).pack(side="left")
        self.quality_combo = ttk.Combobox(options, textvariable=self.quality, values=list(QUALITY), state="readonly", width=21)
        self.quality_combo.pack(side="left"); self.quality_combo.bind("<<ComboboxSelected>>", self.quality_changed)
        ttk.Label(options, text="Overlay").pack(side="left",padx=(24,10))
        self.overlay_combo = ttk.Combobox(options, textvariable=self.overlay_mode, values=list(OVERLAYS), state="readonly", width=31)
        self.overlay_combo.pack(side="left"); self.overlay_combo.bind("<<ComboboxSelected>>", self.overlay_changed)
        self.scenerow = ttk.Frame(main)
        ttk.Label(self.scenerow, text="Scene", width=9).pack(side="left")
        self.scene_entry = ttk.Entry(self.scenerow, textvariable=self.scene_path)
        self.scene_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.scene_button = ttk.Button(self.scenerow, text="Choose…", command=self.choose_scene)
        self.scene_button.pack(side="left", padx=(8,0))
        self.outrow = ttk.Frame(main); self.outrow.pack(fill="x", pady=(2,10))
        ttk.Label(self.outrow, text="Save to", width=9).pack(side="left")
        self.output_entry = ttk.Entry(self.outrow, textvariable=self.output); self.output_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.output_button = ttk.Button(self.outrow, text="Choose…", command=self.choose_output); self.output_button.pack(side="left", padx=(8,0))
        self.progress = ttk.Progressbar(main, maximum=100); self.progress.pack(fill="x", pady=(2,7))
        ttk.Label(main, textvariable=self.status).pack(anchor="w")
        actions = ttk.Frame(main); actions.pack(fill="x", pady=(12,0))
        self.create = ttk.Button(actions, text="Create files", style="Accent.TButton", command=self.begin_export, state="disabled")
        self.create.pack(side="left")
        self.stop = ttk.Button(actions, text="Cancel", command=self.cancel_work, state="disabled"); self.stop.pack(side="left", padx=8)
        self.open_result = ttk.Button(actions, text="Open folder", command=lambda: open_path(self.result), state="disabled"); self.open_result.pack(side="right")
        self.open_report = ttk.Button(actions, text="Report", command=lambda: open_path(self.result / "Report.html"), state="disabled"); self.open_report.pack(side="right", padx=8)
        self.folder.trace_add("write", self.folder_changed)
        self.folder_changed(); self.overlay_changed()
        root.protocol("WM_DELETE_WINDOW", self.close); root.after(100, self.poll)
        if not self.welcomed:
            root.after(250, self.welcome)

    def open_help(self):
        open_path(asset("quick-start.html"))

    def open_download_page(self):
        webbrowser.open(DOWNLOAD_PAGE)

    def uninstall(self):
        if self.busy:
            messagebox.showinfo("App is busy", "Finish or cancel the current operation before uninstalling.", parent=self.root)
            return
        command = uninstall_command()
        if command is None:
            messagebox.showinfo("Uninstall app",
                "This copy has no installer to uninstall. For a portable copy, close the app and delete its extracted app folder.\n\n"
                "If you installed it using Setup, use Windows Settings > Apps > GoPro VBOX Sync. "
                "Keep your recordings and exports.", parent=self.root)
            return
        try:
            subprocess.Popen(command, cwd=str(Path(command[0]).parent), close_fds=True)
        except OSError as exc:
            messagebox.showerror("Could not open uninstaller",
                "Use Windows Settings > Apps > GoPro VBOX Sync to uninstall.\n\n" + str(exc), parent=self.root)
            return
        self.finish_close()

    def about(self):
        messagebox.showinfo("About GoPro VBOX Sync",
            f"GoPro VBOX Sync {__version__} · beta\n\n"
            "Free and open source under the MIT licence. No account or activation.\n\n"
            "Independent project, not affiliated with GoPro or Racelogic. "
            "The quick-start guide includes privacy details and third-party licences.", parent=self.root)

    def welcome(self):
        window = tk.Toplevel(self.root); window.title("Welcome to GoPro VBOX Sync")
        window.transient(self.root); window.resizable(False, False)
        body = ttk.Frame(window, padding=24); body.pack(fill="both", expand=True)
        intro = ttk.Frame(body); intro.pack(fill="x")
        ttk.Label(intro, image=self.welcome_brand, padding=0).pack(side="left", padx=(0,16))
        ttk.Label(intro, text="Your GoPro video. Your VBOX data.", font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(body, text="1. Put the GoPro MP4 files in the folder with your VBOX runs.\n"
                  "2. Scan, choose the videos to include and check rotation.\n"
                  "3. Preview the overlay, then create your files.", padding=(0,16), justify="left").pack(anchor="w")
        actions = ttk.Frame(body); actions.pack(fill="x")
        def finish(action=None):
            self.welcomed = True; self.save_preferences(); window.destroy()
            if action: action()
        ttk.Button(actions, text="Try demo", style="Accent.TButton", command=lambda: finish(self.try_demo)).pack(side="left")
        ttk.Button(actions, text="Read guide", command=self.open_help).pack(side="left", padx=8)
        ttk.Button(actions, text="Get started", command=lambda: finish(self.choose_folder)).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", finish)
        window.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_reqwidth()) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_reqheight()) // 2)
        window.geometry(f"+{x}+{y}")

    def setup_tools(self, after=None):
        if self.busy:
            return
        if tools_ready():
            if after: after()
            else: messagebox.showinfo("Video tools ready", "FFmpeg and FFprobe are available. You can scan and export.")
            return
        if not messagebox.askokcancel("Set up video tools",
            "FFmpeg and FFprobe are needed to read and create video.\n\n"
            "Download both now (110 MB) from Gyan's GitHub release? "
            "The app verifies the download and saves the tools for your Windows account. "
            "Allow about 500 MB of free space. No recordings are uploaded.\n\n"
            "FFmpeg is separate GPLv3 software; its licence is included in the download.", parent=self.root):
            return
        self.status.set("Downloading video tools…"); self.progress["value"] = 0
        def work():
            download_tools(lambda value: self.events.put(("tools_progress", value)), self.cancel)
            self.events.put(("tools_ready", after))
        self.worker(work)

    def try_demo(self):
        if self.busy:
            return
        if not tools_ready():
            self.setup_tools(self.try_demo); return
        self.scene_path.set(""); self.overlay_mode.set("Driving data")
        self.folder.set(str(copy_demo())); self.overlay_changed(); self.begin_scan()

    def log(self, message):
        self.log_messages.append(message)

    def show_details(self):
        window=tk.Toplevel(self.root);window.title("Scan and export details");window.geometry("820x450")
        text=tk.Text(window,wrap="word",padx=16,pady=16,font=("Consolas",10))
        text.pack(fill="both",expand=True)
        text.insert("end", "\n".join(self.log_messages) or "No scan yet.");text.configure(state="disabled")

    def folder_changed(self, *_):
        if not self.busy:
            self.scan = None; self.create.configure(state="disabled")
            self.included_videos.clear(); self.tree.delete(*self.tree.get_children()); self.summary.set("")
            self.crops.clear(); self.crop_choice.set("No crop"); self.crop_note.set("")
            self.preview_generation += 1
            self.preview_label.configure(image="", text="Preview")
            self.set_busy(False)
            self.output.set(str(Path(self.folder.get()) / self.output_name()) if self.folder.get() else "")

    def update_selection_summary(self):
        if self.scan is None:
            return
        matches = [m for m in self.scan.matches if m.video.path.name in self.included_videos]
        seconds = sum(m.rows[-1].utc - m.rows[0].utc for m in matches)
        self.summary.set(f"{len(self.scan.videos)} videos · {len(self.included_videos)} included · {seconds / 60:.1f} minutes of data"
                         + (f" · {len(self.scan.errors)} issues — see Details" if self.scan.errors else ""))

    def toggle_include(self, row):
        if self.busy or self.scan is None or not row:
            return
        video = self.scan.videos[int(row)]
        if not any(m.video is video for m in self.scan.matches):
            return
        name = video.path.name
        if name in self.included_videos:
            self.included_videos.remove(name)
        else:
            self.included_videos.add(name)
        self.tree.set(row, "include", "☑" if name in self.included_videos else "☐")
        self.update_selection_summary(); self.quality_changed(); self.set_busy(False)

    def click_include(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) == "#1":
            self.toggle_include(self.tree.identify_row(event.y))
            return "break"

    def toggle_focused_include(self, event=None):
        self.toggle_include(self.tree.focus())
        return "break"

    def output_name(self):
        name = "GoPro Circuit Tools Overlay" if OVERLAYS[self.overlay_mode.get()] != "none" else "GoPro Circuit Tools"
        if any(mode != "none" for key, mode in self.crops.items() if key in self.included_videos):
            name += " Cropped"
        return name

    def quality_changed(self, *_):
        defaults = {str(Path(self.folder.get()) / (name + suffix)) for name in ("GoPro Circuit Tools", "GoPro Circuit Tools Original", "GoPro Circuit Tools Overlay") for suffix in ("", " Cropped")}
        if self.folder.get() and self.output.get() in defaults:
            self.output.set(str(Path(self.folder.get()) / self.output_name()))

    def overlay_changed(self, *_):
        mode=OVERLAYS[self.overlay_mode.get()]
        if mode != "none":
            self.scenerow.pack(fill="x", pady=(0,10), before=self.outrow)
            if mode == "full" and not self.scene_path.get().strip():
                folder=Path(self.folder.get()).parent / "Scenes"
                candidates=list(folder.glob("*.VVHSN")) if folder.is_dir() else []
                if len(candidates)==1:self.scene_path.set(str(candidates[0]))
        else:self.scenerow.pack_forget()
        self.quality_changed(); self.set_busy(self.busy)

    def choose_scene(self):
        initial = Path(self.folder.get()).parent / "Scenes"
        name = filedialog.askopenfilename(title="Choose VBOX HD2 scene", initialdir=str(initial) if initial.is_dir() else None,
                                          filetypes=[("VBOX HD2 scene", "*.vvhsn *.VVHSN")])
        if name:
            self.scene_path.set(name)
            self.preview_overlay()

    def preview_overlay(self):
        video = self.selected_video()
        if not video:
            return
        mode=OVERLAYS[self.overlay_mode.get()]
        path = self.scene_path.get().strip() if mode != "none" else ""
        if mode == "full" and not path:
            messagebox.showerror("Choose a scene", "Choose a VBOX scene for the full overlay."); return
        rotation = self.rotations.get(video.path.name, video.orientation.clockwise)
        if rotation is None:
            messagebox.showerror("Choose rotation", "Check the video orientation before previewing gauges."); return
        scan, size = self.scan, QUALITY[self.quality.get()]
        crop = self.video_crop(video, rotation)
        destination = Path(self.temporary.name) / "overlay-preview.png"
        self.status.set("Reading scene artwork and preparing a preview…")
        def work():
            from PIL import Image
            from .overlay import load_scene, Renderer
            from .engine import dimensions
            matches = [m for m in scan.matches if m.video is video]
            if not matches:
                raise ValueError("This video has no matching VBOX data")
            scene = load_scene(Path(path), mode=mode) if path else None
            w, h = dimensions(video, rotation, size or 0, crop)
            renderer = Renderer(video, matches, w, h, scene) if mode != "none" else None
            seconds = video.clock.media_time((matches[0].rows[0].utc + matches[0].rows[-1].utc) / 2)
            # Render at export size first, then reduce the finished preview.
            preview(video.path, seconds, rotation, destination, crop=crop, output_size=(w, h))
            im = Image.open(destination).convert("RGBA")
            if renderer is not None: im.alpha_composite(renderer.frame(seconds), renderer.position)
            im.thumbnail((1100, 800), Image.Resampling.LANCZOS); im.save(destination)
            note = "Full scene · rear camera excluded" if mode == "full" else "Driving data" if mode == "four" else "Video only"
            self.events.put(("overlay_preview", (destination, note)))
        self.worker(work)

    def choose_folder(self):
        value = filedialog.askdirectory(title="Choose the folder with your VBOX runs and GoPro MP4 files", initialdir=self.folder.get() or None)
        if value:
            self.folder.set(value)

    def choose_output(self):
        value = filedialog.askdirectory(title="Choose where to place the new output folder", initialdir=self.folder.get() or None)
        if value:
            self.output.set(str(Path(value) / self.output_name()))

    def set_busy(self, busy):
        self.busy = busy
        self.help_menu.entryconfigure("Uninstall app…", state="disabled" if busy else "normal")
        for widget in (self.scan_button, self.browse, self.source_entry, self.output_entry, self.output_button):
            widget.configure(state="disabled" if busy else "normal")
        self.quality_combo.configure(state="disabled" if busy else "readonly")
        self.overlay_combo.configure(state="disabled" if busy else "readonly")
        enabled = OVERLAYS[self.overlay_mode.get()] != "none" and not busy
        self.scene_entry.configure(state="normal" if enabled else "disabled")
        self.scene_button.configure(state="normal" if enabled else "disabled")
        self.overlay_preview.configure(state="normal" if not busy and self.scan and self.scan.matches else "disabled")
        self.rotate_combo.configure(state="disabled" if busy or not self.scan else "readonly")
        video = self.selected_video()
        self.crop_combo.configure(state="readonly" if not busy and video and video.path.name in self.scan.crop_references else "disabled")
        self.create.configure(state="disabled" if busy or not self.scan or not self.included_videos else "normal")
        self.stop.configure(state="normal" if busy else "disabled")
        self.tree.configure(selectmode="none" if busy else "browse")

    def worker(self, function):
        self.cancel.clear(); self.set_busy(True)
        def work():
            try:
                function()
            except InterruptedError as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("error", (str(exc), traceback.format_exc())))
            finally:
                self.events.put(("idle", None))
        Thread(target=work, daemon=True).start()

    def begin_scan(self):
        if not tools_ready():
            self.setup_tools(self.begin_scan); return
        folder = Path(self.folder.get())
        if not self.folder.get() or not folder.is_dir():
            messagebox.showerror("Choose a folder", "Choose the folder with your VBOX runs and GoPro MP4 files."); return
        self.result = None; self.rotations = {}; self.crops = {}; self.progress["value"] = 0
        self.crop_choice.set("No crop"); self.crop_note.set("")
        self.scan = None; self.included_videos.clear(); self.summary.set("")
        self.preview_generation += 1; self.preview_label.configure(image="", text="Preview")
        self.open_result.configure(state="disabled"); self.open_report.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.status.set("Reading GPS timestamps and checking video orientation…")
        self.worker(lambda: self.events.put(("scan", scan_folder(folder, lambda m: self.events.put(("log", m)), self.cancel))))

    def selected_video(self):
        selection = self.tree.selection()
        return self.scan.videos[int(selection[0])] if selection and self.scan else None

    def select_video(self, *_):
        video = self.selected_video()
        if video is None or self.busy:
            return
        value = self.rotations.get(video.path.name, video.orientation.clockwise)
        self.rotation.set(next((k for k, v in ROTATIONS.items() if v == value), "Choose rotation after reviewing"))
        mode = self.crops.get(video.path.name, "none")
        self.crop_choice.set(next(k for k, v in CROPS.items() if v == mode))
        self.crop_combo.configure(state="readonly" if video.path.name in self.scan.crop_references else "disabled")
        if video.path.name in self.scan.crop_errors:
            self.log(self.scan.crop_errors[video.path.name])
        self.log(f"{video.path.name}: {video.orientation.evidence}")
        self.load_preview(video, value or 0)

    def change_rotation(self, *_):
        video = self.selected_video()
        if video and self.rotation.get() in ROTATIONS:
            value = ROTATIONS[self.rotation.get()]
            self.rotations[video.path.name] = value
            self.load_preview(video, value)

    def change_crop(self, *_):
        video = self.selected_video()
        if video and not self.busy:
            self.crops[video.path.name] = CROPS[self.crop_choice.get()]
            self.quality_changed()
            self.load_preview(video, self.rotations.get(video.path.name, video.orientation.clockwise) or 0)

    def video_crop(self, video, rotation):
        mode = self.crops.get(video.path.name, "none")
        if mode == "none":
            return None
        ref = self.scan.crop_references.get(video.path.name)
        if ref is None:
            raise ValueError("Scan again with the original VBOX video in the recordings folder to use cropping")
        return rectangle(video.width, video.height, rotation, ref.aspect, mode)

    def load_preview(self, video, rotation):
        self.preview_generation += 1
        generation = self.preview_generation
        destination = Path(self.temporary.name) / f"frame-{generation}.png"
        match = next((m for m in self.scan.matches if m.video is video), None)
        seconds = (video.clock.media_time(match.rows[0].utc) + 5) if match else 30
        seconds = min(seconds, video.duration * .8)
        crop = self.video_crop(video, rotation)
        ref = self.scan.crop_references.get(video.path.name)
        from .engine import dimensions
        output_size = dimensions(video, rotation, QUALITY[self.quality.get()], crop)
        note = f"VBOX {ref.label}" if ref else "VBOX crop needs the original VBOX video. See Details."
        if crop:
            upright = (video.height, video.width) if rotation % 180 else (video.width, video.height)
            note += " · sides trimmed equally" if crop.width < upright[0] - 1 else " · cropped preview"
            if (crop.width, crop.height) == upright:
                note = f"VBOX {ref.label} · already the same shape"
        self.crop_note.set(note)
        self.preview_label.configure(image="", text="Loading preview…")
        def work():
            try:
                preview(video.path, seconds, rotation, destination, crop=crop, output_size=output_size)
                with Image.open(destination) as frame:
                    frame.thumbnail((360, 240), Image.Resampling.LANCZOS)
                    frame.save(destination)
                self.events.put(("preview", (generation, destination)))
            except Exception as exc:
                self.events.put(("preview_failed", generation))
                self.events.put(("log", f"Preview unavailable: {exc}"))
        Thread(target=work, daemon=True).start()

    def begin_export(self):
        if self.scan is None:
            return
        scan, output = self.scan, Path(self.output.get())
        included = set(self.included_videos)
        if not included:
            messagebox.showerror("Choose videos", "Tick at least one video to include in processing."); return
        rotations, size = dict(self.rotations), QUALITY[self.quality.get()]
        crops = dict(self.crops)
        mode = OVERLAYS[self.overlay_mode.get()]
        overlay_enabled = mode != "none"
        scene = Path(self.scene_path.get().strip()) if overlay_enabled and self.scene_path.get().strip() else None
        if mode == "full" and scene is None:
            messagebox.showerror("Choose a scene", "Choose a VBOX scene for the full overlay."); return
        self.status.set("Preparing video and data…")
        self.progress["value"] = 0
        self.worker(lambda: self.events.put(("complete", export(scan, output, rotations=rotations, max_size=size,
            include_videos=included, crops=crops,
            telemetry_overlay=overlay_enabled, overlay_scene=scene, overlay_mode=mode if mode != "none" else "four",
            log=lambda m: self.events.put(("log", m)), progress=lambda v: self.events.put(("progress", v)), cancel=self.cancel))))

    def cancel_work(self):
        self.cancel.set(); self.stop.configure(state="disabled")
        self.status.set("Cancelling safely…")

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log(value)
                elif kind == "overlay_preview":
                    destination, note = value
                    if not self.closing:
                        window = tk.Toplevel(self.root); window.title("Output preview")
                        window.image = tk.PhotoImage(file=str(destination))
                        ttk.Label(window, image=window.image).pack()
                        ttk.Label(window, text=note, padding=12, wraplength=1000).pack(fill="x")
                    self.status.set("Preview ready.")
                elif kind == "progress":
                    self.progress["value"] = value * 100
                    self.status.set(f"Preparing and verifying files · {value:.0%}")
                elif kind == "tools_progress":
                    self.progress["value"] = value * 100
                    self.status.set(f"Downloading and checking video tools · {value:.0%}")
                elif kind == "tools_ready":
                    self.status.set("Video tools ready. Choose a folder and scan.")
                    if value and not self.closing: self.root.after(0, value)
                elif kind == "preview":
                    generation, destination = value
                    if generation == self.preview_generation and not self.closing:
                        self.preview_image = tk.PhotoImage(file=str(destination))
                        self.preview_label.configure(image=self.preview_image, text="")
                elif kind == "preview_failed":
                    if value == self.preview_generation:
                        self.preview_label.configure(image="", text="Preview unavailable. Select this recording again to retry.")
                elif kind == "scan":
                    self.scan = value
                    self.included_videos = {m.video.path.name for m in value.matches}
                    self.update_selection_summary()
                    for i, video in enumerate(value.videos):
                        matches = [m for m in value.matches if m.video is video]
                        overlap = sum(m.rows[-1].utc - m.rows[0].utc for m in matches)
                        self.tree.insert("", "end", iid=str(i), values=("☑" if matches else "—", video.path.name, ", ".join(dict.fromkeys(m.vbo.path.name for m in matches)) or "No match", f"{overlap / 60:.2f} min"))
                    for err in value.errors:
                        self.log("ISSUE: " + err)
                    for name in value.summary()["unmatched_vbo"]:
                        self.log(f"No GoPro overlap: {name}")
                    self.status.set("Ready. Check the preview before exporting." if value.matches else "No matches. See Details.")
                elif kind == "complete":
                    self.result = value
                    self.status.set("Files created. Open the folder or view the report.")
                    self.open_result.configure(state="normal"); self.open_report.configure(state="normal")
                elif kind == "cancelled":
                    self.status.set("Cancelled. Original files are unchanged; diagnostics remain in the working folder.")
                elif kind == "error":
                    text, trace = value
                    self.log(text); self.status.set("Could not complete. See Details.")
                    self.preferences.parent.mkdir(parents=True, exist_ok=True)
                    (self.preferences.parent / "last-error.log").write_text(trace, encoding="utf-8")
                    if not self.closing:
                        messagebox.showerror("GoPro VBOX Sync", text)
                elif kind == "idle":
                    self.set_busy(False)
                    if self.closing:
                        self.finish_close(); return
                    if self.scan and self.tree.get_children() and not self.tree.selection():
                        self.tree.selection_set(self.tree.get_children()[0]); self.select_video()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            self.closing = True; self.cancel_work()
        else:
            self.finish_close()

    def finish_close(self):
        self.save_preferences()
        self.root.destroy()

    def save_preferences(self):
        try:
            self.preferences.parent.mkdir(parents=True, exist_ok=True)
            self.preferences.write_text(json.dumps({"folder": self.folder.get(), "quality": self.quality.get(),
                                                    "overlay_mode": self.overlay_mode.get(), "scene_path": self.scene_path.get(),
                                                    "welcomed": self.welcomed}), encoding="utf-8")
        except OSError:
            pass


def launch(folder: Path | None = None):
    root = tk.Tk()
    App(root, folder)
    root.mainloop()
