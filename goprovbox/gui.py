"""Threaded desktop interface; all Tk access stays on the UI thread."""
from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from copy import deepcopy
import json
import math
import os
import queue
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import traceback
import webbrowser
from PIL import Image, ImageDraw, ImageTk

from . import __version__
from .engine import scan_folder, export
from .reports import export_report_path
from .crop import rectangle
from .runinfo import video_run_info
from .selection import TIME_BASES, lap_time, moment, row_values, recording_details
from .distribution import (asset, data_directory, tools_ready, download_tools, copy_demo,
                           uninstall_command, DOWNLOAD_PAGE, RELEASES, REPOSITORY)

ROTATIONS = {"Upright — no rotation": 0, "90° clockwise": 90, "180°": 180, "90° anticlockwise": 270}
QUALITY = {"HD · 1920 px": 1920, "Full resolution": 0, "Compact · 1280 px": 1280}
OVERLAYS = {"None": "none", "Driving data": "four", "Full scene · no rear camera": "full"}
SOUND = {"VBOX (GoPro for gaps)": "vbox", "GoPro": "gopro"}
TABLE_COLUMNS = (("include", "Include", 58), ("video", "GoPro", 126), ("vbox", "VBOX", 112),
                 ("date", "Date", 94), ("start", "Start", 82),
                 ("lap", "Best in file", 102), ("overlap", "Overlap", 82))
FASTEST_COLOR = "#c62828"



def open_path(path: Path):
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if __import__("sys").platform == "darwin" else "xdg-open", str(path)])


def fastest_star(root):
    size = max(14, round(root.winfo_fpixels("12p")))
    scale = size * 4
    icon = Image.new("RGBA", (scale, scale))
    points = []
    for point in range(10):
        angle = -math.pi / 2 + point * math.pi / 5
        radius = scale * (.46 if point % 2 == 0 else .20)
        points.append((scale / 2 + math.cos(angle) * radius, scale / 2 + math.sin(angle) * radius))
    ImageDraw.Draw(icon).polygon(points, fill=FASTEST_COLOR)
    return ImageTk.PhotoImage(icon.resize((size, size), Image.Resampling.LANCZOS), master=root)


class App:
    def __init__(self, root: tk.Tk, folder: Path | None = None, *,
                 gopro_folder: Path | None = None, recursive_gopro: bool | None = None):
        self.root = root
        self.events = queue.Queue()
        self.cancel = Event()
        self.scan = None
        self.result = None
        self.busy = False
        self.closing = False
        self.rotations = {}
        self.crop_choice = "none"
        self.included_videos = set()
        self.preview_generation = 0
        self.preview_image = None
        self.preview_source = None
        self.preview_display_size = None
        self.preview_cancel = Event()
        self.preview_request = None
        self.preview_video = None
        self.selection_data = {}
        self.sort_column = "start"
        self.sort_reverse = False
        self.temporary = tempfile.TemporaryDirectory(prefix="goprovbox-preview-")
        self.preferences = data_directory() / "settings.json"
        try:
            prefs = json.loads(self.preferences.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prefs = {}
        self.welcomed = bool(prefs.get("welcomed", False))
        self.folder = tk.StringVar(value=str(folder or prefs.get("folder", "")))
        saved_gopro = "" if prefs.get("same_gopro_folder", False) else str(prefs.get("gopro_folder") or "")
        chosen_gopro = str(gopro_folder) if gopro_folder is not None else saved_gopro
        self._gopro_folder_automatic = gopro_folder is None and not chosen_gopro.strip()
        self._setting_gopro_default = False
        self.gopro_folder = tk.StringVar(value=self.folder.get() if self._gopro_folder_automatic else chosen_gopro)
        self.recursive_gopro = tk.BooleanVar(value=prefs.get("include_gopro_subfolders", True)
                                           if recursive_gopro is None else recursive_gopro)
        self.output = tk.StringVar()
        self.quality = tk.StringVar(value=prefs.get("quality", next(iter(QUALITY))))
        legacy = {"Full resolution · re-encode": "Full resolution", "Original resolution": "Full resolution",
                  "Compact · re-encode at 1280px": "Compact · 1280 px", "Compact · 1280px longest edge": "Compact · 1280 px"}
        self.quality.set(legacy.get(self.quality.get(), self.quality.get()))
        if self.quality.get() not in QUALITY:
            self.quality.set(next(iter(QUALITY)))
        self.rotation = tk.StringVar(value=next(iter(ROTATIONS)))
        self.crop_note = tk.StringVar()
        self.overlap_only = tk.BooleanVar(value=prefs.get("overlap_only", True))
        self.audio_source = tk.StringVar(value=next((label for label, value in SOUND.items()
                                                    if value == prefs.get("audio_source", "vbox")), next(iter(SOUND))))
        self.overlay_mode = tk.StringVar(value=prefs.get("overlay_mode", "Driving data" if prefs.get("overlay_enabled") else "None"))
        if self.overlay_mode.get() not in OVERLAYS: self.overlay_mode.set("None")
        self.scene_path = tk.StringVar(value=prefs.get("scene_path", ""))
        self.time_basis = tk.StringVar(value=prefs.get("time_basis", "Local time"))
        if self.time_basis.get() not in TIME_BASES:
            self.time_basis.set("Local time")
        self.run_detail = tk.StringVar()
        self.export_video_name = tk.StringVar()
        self.preview_title = tk.StringVar(value="Tick a video to preview its export")
        self.status = tk.StringVar(value="Choose both folders above, then click Scan for matches.")
        self.summary = tk.StringVar(value="")
        root.title(f"GoPro VBOX Sync {__version__}")
        width = min(1320, max(880, root.winfo_screenwidth() - 80))
        height = min(860, max(740, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}"); root.minsize(880, 740)
        root.configure(bg="#f4f6f8")
        if asset("icon.png").is_file():
            self.app_icon = tk.PhotoImage(file=str(asset("icon.png")))
            root.iconphoto(True, self.app_icon)
        style = ttk.Style(root); style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f4f6f8", foreground="#203047")
        style.configure("TButton", padding=(12, 7))
        style.configure("Accent.TButton", background="#087f74", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#04675e"), ("disabled", "#b6c7c5")])
        style.configure("Treeview", rowheight=32, indent=0, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=6)
        style.map("Treeview", background=[("selected", "#d1ebe5")], foreground=[("selected", "#15392f")])
        style.configure("TProgressbar", background="#078b7e", troughcolor="#dce3e9")
        header = tk.Frame(root, bg="#172d42", padx=22, pady=10); header.pack(fill="x")
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
        self.build_options(header)
        main = ttk.Frame(root, padding=(20,12)); main.pack(fill="both", expand=True)
        ttk.Label(main, text="Choose recording folders", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0,6))
        line = ttk.Frame(main); line.pack(fill="x")
        ttk.Label(line, text="VBOX runs", width=12).pack(side="left")
        self.source_entry = ttk.Entry(line, textvariable=self.folder); self.source_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.browse = ttk.Button(line, text="Browse…", command=self.choose_folder); self.browse.pack(side="left", padx=(8,0))
        goprorow = ttk.Frame(main); goprorow.pack(fill="x", pady=(6,0))
        ttk.Label(goprorow, text="GoPro videos", width=12).pack(side="left")
        self.gopro_entry = ttk.Entry(goprorow, textvariable=self.gopro_folder)
        self.gopro_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.gopro_browse = ttk.Button(goprorow, text="Browse…", command=self.choose_gopro_folder)
        self.gopro_browse.pack(side="left", padx=(8,0))
        sources = ttk.Frame(main); sources.pack(fill="x", pady=(6,0))
        self.scan_button = ttk.Button(sources, text="Scan for matches", style="Accent.TButton", command=self.begin_scan)
        self.scan_button.pack(side="right")
        ttk.Label(sources, text="Find matching runs before choosing\nvideos to convert.", justify="left",
                  foreground="#526478").pack(side="right", padx=(12,16))
        summaryrow = ttk.Frame(main); summaryrow.pack(fill="x", pady=(8,6))
        ttk.Label(summaryrow, textvariable=self.summary).pack(side="left")
        self.time_combo = ttk.Combobox(summaryrow, textvariable=self.time_basis, values=TIME_BASES,
                                      state="readonly", width=11)
        self.time_combo.pack(side="right")
        ttk.Label(summaryrow, text="Times").pack(side="right", padx=(12,8))
        footer = ttk.Frame(main, padding=(0,12,0,0)); footer.pack(side="bottom", fill="x")
        self.panes = panes = ttk.Panedwindow(main, orient="horizontal"); panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes); right = ttk.Frame(panes, padding=(16,0,0,0))
        panes.add(left, weight=4); panes.add(right, weight=2)
        table = ttk.Frame(left); table.pack(fill="both", expand=True)
        table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(c[0] for c in TABLE_COLUMNS),
                                 show="tree headings", height=5, selectmode="browse")
        self.fastest_lap_star = fastest_star(root)
        self.tree.column("#0", width=28, minwidth=28, stretch=False, anchor="center")
        self.tree.heading("#0", text="")
        for key, text, width in TABLE_COLUMNS:
            self.tree.heading(key, text=text, command=lambda column=key: self.sort_rows(column))
            self.tree.column(key, width=width, minwidth=width, stretch=key in ("video", "vbox"),
                             anchor="center" if key == "include" else "w")
        self.tree.tag_configure("fastest", background="#e6f3eb")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self.select_video)
        self.tree.bind("<Button-1>", self.click_include)
        self.tree.bind("<space>", self.toggle_focused_include)
        self.log_messages = []
        preview_controls = ttk.Frame(right); preview_controls.pack(side="bottom", fill="x")
        exportrow = ttk.Frame(right); exportrow.pack(fill="x", pady=(0,6))
        ttk.Label(exportrow, text="Preview video").pack(side="left", padx=(0,8))
        self.export_video_combo = ttk.Combobox(exportrow, textvariable=self.export_video_name,
                                              state="disabled", width=18)
        self.export_video_combo.pack(side="left", fill="x", expand=True)
        self.export_video_combo.bind("<<ComboboxSelected>>", self.choose_export_video)
        self.preview_heading = ttk.Label(right, textvariable=self.preview_title, font=("Segoe UI", 10, "bold"),
                                         wraplength=360, justify="left")
        self.preview_heading.pack(fill="x", pady=(0,6))
        self.preview_label = ttk.Label(right, text="Export preview", anchor="center", background="#dce3e9", wraplength=220)
        self.preview_label.pack(fill="both", expand=True)
        self.preview_label.bind("<Configure>", self.resize_preview)
        rotationrow = ttk.Frame(preview_controls); rotationrow.pack(fill="x", pady=(8,0))
        self.rotate_combo = ttk.Combobox(rotationrow, textvariable=self.rotation, values=list(ROTATIONS), state="disabled", width=22)
        self.rotate_combo.pack(side="left",fill="x",expand=True); self.rotate_combo.bind("<<ComboboxSelected>>", self.change_rotation)
        self.overlay_preview = ttk.Button(rotationrow, text="Frame & preview…", command=self.preview_overlay, state="disabled")
        self.overlay_preview.pack(side="left",padx=(8,0))
        ttk.Label(preview_controls, textvariable=self.crop_note, wraplength=360).pack(anchor="w", pady=(4,0))
        ttk.Label(preview_controls, textvariable=self.run_detail, wraplength=360, justify="left").pack(anchor="w", pady=(4,0))
        self.scenerow = ttk.Frame(footer)
        ttk.Label(self.scenerow, text="Scene", width=9).pack(side="left")
        self.scene_entry = ttk.Entry(self.scenerow, textvariable=self.scene_path)
        self.scene_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.scene_entry.bind("<FocusOut>", self.refresh_export_preview)
        self.scene_entry.bind("<Return>", self.refresh_export_preview)
        self.scene_button = ttk.Button(self.scenerow, text="Choose…", command=self.choose_scene)
        self.scene_button.pack(side="left", padx=(8,0))
        self.outrow = ttk.Frame(footer); self.outrow.pack(fill="x", pady=(2,10))
        ttk.Label(self.outrow, text="Save to", width=9).pack(side="left")
        self.output_entry = ttk.Entry(self.outrow, textvariable=self.output); self.output_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.output_button = ttk.Button(self.outrow, text="Choose…", command=self.choose_output); self.output_button.pack(side="left", padx=(8,0))
        self.progress = ttk.Progressbar(footer, maximum=100); self.progress.pack(fill="x", pady=(2,7))
        ttk.Label(footer, textvariable=self.status).pack(anchor="w")
        actions = ttk.Frame(footer); actions.pack(fill="x", pady=(8,0))
        self.create = ttk.Button(actions, text="Create files", style="Accent.TButton", command=self.begin_export, state="disabled")
        self.create.pack(side="left")
        self.stop = ttk.Button(actions, text="Cancel", command=self.cancel_work, state="disabled"); self.stop.pack(side="left", padx=8)
        self.open_result = ttk.Button(actions, text="Open folder", command=lambda: open_path(self.result), state="disabled"); self.open_result.pack(side="right")
        self.open_report = ttk.Button(actions, text="Report", command=self.show_report, state="disabled"); self.open_report.pack(side="right", padx=8)
        self.folder.trace_add("write", self.folder_changed)
        self.gopro_folder.trace_add("write", self.gopro_folder_changed)
        self.recursive_gopro.trace_add("write", self.source_changed)
        self.time_basis.trace_add("write", self.refresh_rows)
        self.folder_changed(); self.overlay_changed()
        root.after_idle(self.place_selection_panes)
        root.protocol("WM_DELETE_WINDOW", self.close); root.after(100, self.poll)
        if not self.welcomed:
            root.after(250, self.welcome)

    def build_options(self, header):
        self.options_button = ttk.Menubutton(header, text="Options")
        self.options_button.pack(side="right")
        self.options_menu = tk.Menu(self.options_button, tearoff=False)
        self.options_button.configure(menu=self.options_menu)
        self.resolution_menu = tk.Menu(self.options_menu, tearoff=False)
        for label in QUALITY:
            self.resolution_menu.add_radiobutton(label=label, value=label, variable=self.quality,
                                                command=self.quality_changed)
        self.overlay_menu = tk.Menu(self.options_menu, tearoff=False)
        for label in OVERLAYS:
            self.overlay_menu.add_radiobutton(label=label, value=label, variable=self.overlay_mode,
                                             command=self.overlay_changed)
        self.sound_menu = tk.Menu(self.options_menu, tearoff=False)
        for label in SOUND:
            self.sound_menu.add_radiobutton(label=label, value=label, variable=self.audio_source)
        for label, menu in (("Resolution", self.resolution_menu), ("Overlay", self.overlay_menu),
                            ("Sound", self.sound_menu)):
            self.options_menu.add_cascade(label=label, menu=menu)
        self.options_menu.add_separator()
        self.options_menu.add_checkbutton(label="Only export video with VBOX data", variable=self.overlap_only,
                                          command=self.quality_changed)
        self.options_menu.add_checkbutton(label="Include GoPro subfolders", variable=self.recursive_gopro)
        self.options_menu.add_separator()
        self.options_menu.add_command(label="Details…", command=self.show_details)
        self.options_menu.add_command(label="What do red stars mean?…", command=self.show_lap_help)

    def show_lap_help(self):
        messagebox.showinfo("Red stars and lap times",
            "Best in file is the fastest complete lap entirely inside that GoPro file, using GPS timestamps "
            "and the VBOX start/finish crossings. A dash means no complete lap is available.\n\n"
            "A red star marks footage of a VBOX run's overall fastest lap. If it spans two GoPro files, "
            "both are starred; include both for the complete lap. Their Best in file times can still differ.\n\n"
            "Open Options → Details for lap times, timestamps, file coverage and the scan log.", parent=self.root)

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
        ttk.Label(body, text="1. Choose your VBOX folder and your GoPro folder or SD card.\n"
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
        self._gopro_folder_automatic = True
        self.folder.set(str(copy_demo())); self.overlay_changed(); self.begin_scan()

    def log(self, message):
        self.log_messages.append(message)

    def show_details(self):
        window=tk.Toplevel(self.root);window.title("Scan and export details");window.geometry("820x450")
        text=tk.Text(window,wrap="word",padx=16,pady=16,font=("Consolas",10))
        text.pack(fill="both",expand=True)
        video = self.selected_video()
        details = recording_details(video.path.name, video_run_info(self.scan, video), self.time_basis.get()) if video else ""
        logs = "\n".join(self.log_messages) or "No scan yet."
        text.insert("end", details + "\n\nScan and export log\n\n" + logs if details else logs)
        text.configure(state="disabled")

    def place_selection_panes(self):
        width = self.panes.winfo_width()
        if width > 1:
            self.panes.sashpos(0, max(360, width - 380))

    def refresh_rows(self, *_):
        if self.scan is None:
            return
        self.selection_data = {}
        for index, video in enumerate(self.scan.videos):
            row = str(index)
            info = video_run_info(self.scan, video)
            self.selection_data[row] = info
            values = row_values(video.path.name, info, video.path.name in self.included_videos, self.time_basis.get())
            tags = ("fastest",) if info["contains_fastest"] else ()
            marker = self.fastest_lap_star if info["contains_fastest"] else ""
            if self.tree.exists(row):
                self.tree.item(row, values=values, tags=tags, image=marker)
            else:
                self.tree.insert("", "end", iid=row, values=values, tags=tags, image=marker)
        self.sort_rows(self.sort_column, self.sort_reverse)
        self.sync_export_preview()

    def sort_rows(self, column, reverse=None):
        if reverse is None:
            reverse = not self.sort_reverse if column == self.sort_column else False
        self.sort_column, self.sort_reverse = column, reverse
        def key(row):
            video = self.scan.videos[int(row)]
            info = self.selection_data[row]
            if column in ("date", "start"):
                return info["utc_start"]
            if column == "lap":
                return info["fastest_lap"]["duration_seconds"] if info["fastest_lap"] else None
            if column == "overlap":
                return info["overlap_seconds"]
            if column == "vbox":
                return ", ".join(info["run_names"]).casefold()
            if column == "include":
                return video.path.name not in self.included_videos
            return video.path.name.casefold()
        if self.scan is not None:
            present, unavailable = [], []
            for row in self.tree.get_children():
                (unavailable if key(row) is None else present).append(row)
            for index, row in enumerate(sorted(present, key=key, reverse=reverse) + unavailable):
                self.tree.move(row, "", index)
        for key_, text, _ in TABLE_COLUMNS:
            self.tree.heading(key_, text=text + (" ▼" if reverse else " ▲") if key_ == column else text)

    def export_candidates(self):
        if self.scan is None:
            return []
        linked = {id(match.video) for match in getattr(self.scan, "matches", ())}
        return sorted((video for video in getattr(self.scan, "videos", ())
                       if video.path.name in self.included_videos and id(video) in linked),
                      key=lambda video: (video.clock.utc(0), video.path.name.casefold()))

    def export_video(self):
        return next((video for video in self.export_candidates()
                     if video.path.name == self.export_video_name.get()), None)

    def clear_export_preview(self):
        self.preview_cancel.set(); self.preview_generation += 1
        self.preview_request = None; self.preview_video = None
        self.export_video_name.set("")
        self.export_video_combo.configure(values=(), state="disabled")
        self.preview_source = None; self.preview_display_size = None
        self.preview_title.set("Tick a video to preview its export")
        self.preview_heading.configure(foreground="#203047")
        self.preview_label.configure(image="", text="Export preview")
        self.rotation.set(next(iter(ROTATIONS))); self.crop_note.set(""); self.run_detail.set("")

    def sync_export_preview(self, prefer=None, refresh=False):
        candidates = self.export_candidates()
        names = tuple(video.path.name for video in candidates)
        current = self.export_video_name.get()
        chosen = prefer if prefer in names else current if current in names else names[0] if names else ""
        if not chosen:
            self.clear_export_preview(); self.set_busy(self.busy)
            return
        self.export_video_combo.configure(values=names)
        self.export_video_name.set(chosen)
        video = self.export_video()
        changed = video is not self.preview_video
        self.preview_video = video
        rotation = self.rotations.get(chosen, 0)
        self.rotation.set(next(label for label, value in ROTATIONS.items() if value == rotation))
        self.update_run_detail(); self.set_busy(self.busy)
        if changed or refresh or self.preview_request is None:
            if refresh:
                self.preview_request = None
            self.load_preview(video, rotation)

    def choose_export_video(self, *_):
        if self.busy:
            return
        self.sync_export_preview()
        video = self.export_video()
        if video:
            row = str(self.scan.videos.index(video))
            self.tree.selection_set(row); self.tree.focus(row); self.tree.see(row)

    def refresh_export_preview(self, *_):
        if self.busy:
            return
        if _:
            self.preview_request = None
        video = self.export_video()
        if video:
            self.load_preview(video, self.rotations.get(video.path.name, 0))

    def update_run_detail(self):
        video = self.export_video()
        if video is None:
            self.run_detail.set("")
            return
        info = video_run_info(self.scan, video)
        best = info["fastest_lap"]
        description = (f"Best in file {lap_time(best['duration_seconds'])} · {info['fastest_run']}"
                       if best else "No complete lap in this file · Options → Details")
        self.run_detail.set("Recorded " + moment(info["utc_start"], self.time_basis.get()) + "\n" + description)

    def resize_preview(self, event=None):
        if self.preview_source is None or self.closing:
            return
        frame = self.preview_source.copy()
        frame.thumbnail((max(1, self.preview_label.winfo_width() - 4),
                         max(1, self.preview_label.winfo_height() - 4)), Image.Resampling.LANCZOS)
        if frame.size == self.preview_display_size:
            return
        self.preview_display_size = frame.size
        self.preview_image = ImageTk.PhotoImage(frame, master=self.root)
        self.preview_label.configure(image=self.preview_image, text="")

    def folder_changed(self, *_):
        if not self.busy:
            if self._gopro_folder_automatic:
                self.set_gopro_default()
            self.source_changed()
            self.output.set(str(Path(self.folder.get()) / self.output_name()) if self.folder.get() else "")

    def set_gopro_default(self):
        self._gopro_folder_automatic = True
        self._setting_gopro_default = True
        try:
            self.gopro_folder.set(self.folder.get())
        finally:
            self._setting_gopro_default = False

    def gopro_folder_changed(self, *_):
        if self._setting_gopro_default:
            return
        self._gopro_folder_automatic = not self.gopro_folder.get().strip()
        self.source_changed()

    def source_changed(self, *_):
        if self.busy:
            return
        if self.crop_choice != "none":
            self.log("Recording sources or scan options changed; saved framing was cleared.")
        self.scan = None; self.result = None
        self.selection_data.clear(); self.run_detail.set("")
        self.included_videos.clear(); self.tree.delete(*self.tree.get_children()); self.summary.set("")
        self.rotations.clear(); self.crop_choice = "none"; self.crop_note.set("")
        self.clear_export_preview()
        self.open_result.configure(state="disabled"); self.open_report.configure(state="disabled")
        self.status.set("Choose both folders above, then click Scan for matches.")
        self.quality_changed()
        self.set_busy(False)

    def update_selection_summary(self):
        if self.scan is None:
            return
        matches = [m for m in self.scan.matches if m.video.path.name in self.included_videos]
        seconds = sum(m.rows[-1].utc - m.rows[0].utc for m in matches)
        self.summary.set(f"{len(self.scan.videos)} videos · {len(self.included_videos)} included · {seconds / 60:.1f} minutes of data"
                         + (f" · {len(self.scan.errors)} issues — Options → Details" if self.scan.errors else ""))

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
        if self.sort_column == "include":
            self.sort_rows("include", self.sort_reverse)
        self.update_selection_summary(); self.sync_export_preview(); self.quality_changed(); self.set_busy(False)

    def click_include(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) == "#1":
            if not self.busy and self.scan is not None:
                row = self.tree.identify_row(event.y)
                if row:
                    self.tree.selection_set(row); self.tree.focus(row); self.tree.focus_set()
                    self.toggle_include(row)
                    self.sync_export_preview(prefer=self.scan.videos[int(row)].path.name)
            return "break"

    def toggle_focused_include(self, event=None):
        row = self.tree.focus()
        if row and not self.busy and self.scan is not None:
            self.tree.selection_set(row)
            self.toggle_include(row)
            self.sync_export_preview(prefer=self.scan.videos[int(row)].path.name)
        return "break"

    def output_name(self):
        name = "GoPro Circuit Tools Overlay" if OVERLAYS[self.overlay_mode.get()] != "none" else "GoPro Circuit Tools"
        if self.crop_choice != "none":
            name += " Cropped"
        if self.overlap_only.get():
            name += " Matched"
        return name

    def quality_changed(self, *_):
        defaults = {str(Path(self.folder.get()) / (name + suffix + trimmed))
                    for name in ("GoPro Circuit Tools", "GoPro Circuit Tools Original", "GoPro Circuit Tools Overlay")
                    for suffix in ("", " Cropped") for trimmed in ("", " Matched")}
        if self.folder.get() and self.output.get() in defaults:
            self.output.set(str(Path(self.folder.get()) / self.output_name()))
        self.refresh_export_preview()

    def overlay_changed(self, *_):
        mode=OVERLAYS[self.overlay_mode.get()]
        if mode != "none":
            self.scenerow.pack(fill="x", pady=(0,10), before=self.outrow)
            if mode == "full" and not self.scene_path.get().strip():
                folder=Path(self.folder.get()).parent / "Scenes"
                candidates=[path for path in folder.iterdir()
                            if path.is_file() and path.suffix.lower() in (".vvhsn", ".gvscene")] if folder.is_dir() else []
                if len(candidates)==1:self.scene_path.set(str(candidates[0]))
        else:self.scenerow.pack_forget()
        self.quality_changed(); self.set_busy(self.busy)

    def choose_scene(self):
        initial = Path(self.folder.get()).parent / "Scenes"
        name = filedialog.askopenfilename(title="Choose a scene", initialdir=str(initial) if initial.is_dir() else None,
                                          filetypes=[("VBOX and saved scenes", "*.vvhsn *.VVHSN *.gvscene *.GVSCENE"),
                                                     ("Saved scene", "*.gvscene *.GVSCENE"),
                                                     ("VBOX HD2 scene", "*.vvhsn *.VVHSN")])
        if name:
            self.scene_path.set(name)
            self.preview_request = None
            self.refresh_export_preview()

    def preview_overlay(self):
        video = self.export_video()
        if not video or self.busy:
            return
        mode=OVERLAYS[self.overlay_mode.get()]
        path = self.scene_path.get().strip() if mode != "none" else ""
        if mode == "full" and not path:
            messagebox.showerror("Choose a scene", "Choose a VBOX scene for the full overlay."); return
        rotation = self.rotations.get(video.path.name, 0)
        scan, size = self.scan, QUALITY[self.quality.get()]
        choice = deepcopy(self.crop_choice)
        ref = scan.crop_references.get(video.path.name)
        if choice != "none" and ref is None:
            messagebox.showerror("Crop unavailable",
                "Keep the original VBOX video beside the VBOX runs, then scan again to adjust this crop.\n\n"
                "You can also choose another Preview video with cropping available to change or turn off the crop for all selected videos.",
                parent=self.root)
            return
        destination = Path(self.temporary.name) / "framing-source.png"
        self.status.set("Preparing the crop editor and output preview…")
        def work():
            from .framing import prepare_preview
            matches = [m for m in scan.matches if m.video is video]
            if not matches and mode != "none":
                raise ValueError("This video has no matching VBOX data")
            prepared = prepare_preview(video, matches, rotation, size, ref.aspect if ref else None,
                                       mode, path, destination, self.cancel)
            self.events.put(("framing_preview", (prepared, choice, scan)))
        self.worker(work)

    def choose_folder(self):
        value = filedialog.askdirectory(title="Choose the folder with your VBOX runs", initialdir=self.folder.get() or None)
        if value:
            self.folder.set(value)

    def choose_gopro_folder(self):
        value = filedialog.askdirectory(title="Choose the SD card or folder with your GoPro videos",
                                        initialdir=self.gopro_folder.get() or self.folder.get() or None)
        if value:
            self.gopro_folder.set(value)

    def choose_output(self):
        value = filedialog.askdirectory(title="Choose where to place the new output folder", initialdir=self.folder.get() or None)
        if value:
            self.output.set(str(Path(value) / self.output_name()))

    def set_busy(self, busy):
        self.busy = busy
        self.help_menu.entryconfigure("Uninstall app…", state="disabled" if busy else "normal")
        for widget in (self.scan_button, self.browse, self.source_entry, self.output_entry, self.output_button):
            widget.configure(state="disabled" if busy else "normal")
        for widget in (self.gopro_entry, self.gopro_browse):
            widget.configure(state="disabled" if busy else "normal")
        self.time_combo.configure(state="disabled" if busy else "readonly")
        for label in ("Resolution", "Overlay", "Sound", "Only export video with VBOX data", "Include GoPro subfolders"):
            self.options_menu.entryconfigure(label, state="disabled" if busy else "normal")
        for menu in (self.resolution_menu, self.overlay_menu, self.sound_menu):
            for index in range(menu.index("end") + 1):
                menu.entryconfigure(index, state="disabled" if busy else "normal")
        enabled = OVERLAYS[self.overlay_mode.get()] != "none" and not busy
        self.scene_entry.configure(state="normal" if enabled else "disabled")
        self.scene_button.configure(state="normal" if enabled else "disabled")
        target = self.export_video()
        self.export_video_combo.configure(state="readonly" if target and not busy else "disabled")
        self.overlay_preview.configure(state="normal" if target and not busy else "disabled")
        self.rotate_combo.configure(state="readonly" if target and not busy else "disabled")
        self.create.configure(state="normal" if target and not busy else "disabled")
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

    def show_report(self):
        try:
            open_path(export_report_path(self.result))
        except OSError as exc:
            messagebox.showerror("Export report", str(exc))

    def begin_scan(self):
        if not tools_ready():
            self.setup_tools(self.begin_scan); return
        folder = Path(self.folder.get())
        if not self.folder.get() or not folder.is_dir():
            messagebox.showerror("Choose VBOX runs", "Choose the folder with your VBOX runs."); return
        if not self.gopro_folder.get().strip():
            self.set_gopro_default()
        gopro_folder = Path(self.gopro_folder.get())
        if not gopro_folder.is_dir():
            messagebox.showerror("Choose GoPro videos", "Choose the SD card or folder with your GoPro videos."); return
        recursive_gopro = self.recursive_gopro.get()
        if self.crop_choice != "none":
            self.log("Starting a new scan; saved framing was cleared. Set framing again after scanning.")
        self.result = None; self.rotations = {}; self.crop_choice = "none"; self.progress["value"] = 0
        self.crop_note.set("")
        self.scan = None; self.included_videos.clear(); self.summary.set("")
        self.selection_data.clear(); self.run_detail.set("")
        self.clear_export_preview()
        self.open_result.configure(state="disabled"); self.open_report.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.status.set("Reading GPS timestamps and checking video orientation…")
        self.worker(lambda: self.events.put(("scan", scan_folder(folder, lambda m: self.events.put(("log", m)), self.cancel,
                                                                gopro_folder=gopro_folder, recursive_gopro=recursive_gopro))))

    def selected_video(self):
        selection = self.tree.selection()
        return self.scan.videos[int(selection[0])] if selection and self.scan else None

    def select_video(self, *_):
        video = self.selected_video()
        if video is None or self.busy:
            return
        if video.path.name not in self.included_videos:
            return
        self.sync_export_preview(prefer=video.path.name)
        if video.path.name in self.scan.crop_errors:
            self.log(self.scan.crop_errors[video.path.name])
        self.log(f"{video.path.name}: {video.orientation.evidence}")

    def change_rotation(self, *_):
        video = self.export_video()
        if video and self.rotation.get() in ROTATIONS:
            value = ROTATIONS[self.rotation.get()]
            self.rotations[video.path.name] = value
            self.load_preview(video, value)

    def video_crop(self, video, rotation):
        mode = self.crop_choice
        if mode == "none":
            return None
        ref = self.scan.crop_references.get(video.path.name)
        if ref is None:
            raise ValueError("Scan again with the original VBOX video in the VBOX runs folder to use cropping")
        return rectangle(video.width, video.height, rotation, ref.aspect, mode)

    def load_preview(self, video, rotation):
        if video is not self.export_video():
            return
        scan = self.scan
        size = QUALITY[self.quality.get()]
        mode = OVERLAYS[self.overlay_mode.get()]
        path = self.scene_path.get().strip() if mode != "none" else ""
        choice = deepcopy(self.crop_choice)
        ref = scan.crop_references.get(video.path.name)
        aspect = ref.aspect if ref else None
        request = (id(scan), id(video), rotation, size, mode, path,
                   str(aspect), json.dumps(choice, sort_keys=True))
        if request == self.preview_request:
            return
        self.preview_cancel.set()
        self.preview_cancel = cancelled = Event()
        self.preview_request = request
        self.preview_generation += 1
        generation = self.preview_generation
        destination = Path(self.temporary.name) / f"frame-{generation}.png"
        matches = [match for match in scan.matches if match.video is video]
        note = f"VBOX {ref.label} · no crop" if ref else "VBOX crop needs the original VBOX video. See Options → Details."
        crop = self.video_crop(video, rotation) if ref else None
        if choice != "none" and ref is None:
            note = "Crop applies to all selected videos · original VBOX video needed for this file."
        elif crop is not None:
            upright = (video.height, video.width) if rotation % 180 else (video.width, video.height)
            note = f"Crop for all selected videos · VBOX {ref.label}"
            if (crop.width, crop.height) == upright:
                note = f"Crop for all selected videos · already {ref.label}"
        self.crop_note.set(note)
        self.preview_title.set("Export preview")
        self.preview_label.configure(image="", text="Rendering export preview…")
        self.preview_source = None; self.preview_display_size = None
        events = self.events
        def work(cache=self.temporary):
            try:
                from .framing import prepare_output_preview
                output_size = prepare_output_preview(video, matches, rotation, size, aspect, mode,
                                                      path, destination, cancelled, choice)
                if not cancelled.is_set():
                    events.put(("export_preview", (generation, destination, output_size)))
            except InterruptedError:
                pass
            except Exception as exc:
                if not cancelled.is_set():
                    events.put(("preview_failed", (generation, str(exc))))
                    events.put(("log", f"{video.path.name}: export preview unavailable: {exc}"))
        Thread(target=work, daemon=True).start()

    def begin_export(self):
        if self.scan is None:
            return
        scan, output = self.scan, Path(self.output.get())
        included = set(self.included_videos)
        if not included:
            messagebox.showerror("Choose videos", "Tick at least one video to include in processing."); return
        rotations = {v.path.name: self.rotations.get(v.path.name, 0) for v in scan.videos}
        size = QUALITY[self.quality.get()]
        crops = {name: deepcopy(self.crop_choice) for name in included} if self.crop_choice != "none" else {}
        missing = sorted(set(crops) - set(scan.crop_references), key=str.casefold)
        if missing:
            messagebox.showerror("Crop unavailable",
                "Keep the original VBOX videos beside the VBOX runs, then scan again to crop these selected videos:\n\n"
                + "\n".join(missing) + "\n\nOr turn off Crop to VBOX for this export.", parent=self.root)
            return
        overlap_only = self.overlap_only.get()
        audio_source = SOUND[self.audio_source.get()]
        mode = OVERLAYS[self.overlay_mode.get()]
        overlay_enabled = mode != "none"
        scene = Path(self.scene_path.get().strip()) if overlay_enabled and self.scene_path.get().strip() else None
        if mode == "full" and scene is None:
            messagebox.showerror("Choose a scene", "Choose a VBOX scene for the full overlay."); return
        for name in sorted(included, key=str.casefold):
            self.log(f"{name}: export framing — " + ("no crop" if crops.get(name, "none") == "none" else "crop enabled"))
        self.status.set("Preparing video and data…")
        self.progress["value"] = 0
        self.worker(lambda: self.events.put(("complete", export(scan, output, rotations=rotations, max_size=size,
            include_videos=included, crops=crops, overlap_only=overlap_only, audio_source=audio_source,
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
                elif kind == "framing_preview":
                    prepared, choice, scan = value
                    if (not self.closing and not self.cancel.is_set() and self.scan is scan
                            and prepared.video is self.export_video()):
                        from .framing import CropEditor
                        def apply(choice, video=prepared.video, rotation=prepared.rotation):
                            if (self.scan is not scan or video is not self.export_video()
                                    or self.rotations.get(video.path.name, 0) != rotation):
                                self.log("Framing was not applied because the export selection changed.")
                                return
                            self.crop_choice = deepcopy(choice)
                            crop_state = "no crop" if choice == "none" else "crop enabled"
                            self.log(f"Crop applied to all selected videos — {crop_state}.")
                            self.preview_request = None
                            self.quality_changed()
                            self.load_preview(video, rotation)
                        CropEditor(self.root, prepared, choice, apply,
                                   scan.crop_errors.get(prepared.video.path.name, ""))
                    self.status.set("Preview ready.")
                elif kind == "progress":
                    self.progress["value"] = value * 100
                    self.status.set(f"Preparing and verifying files · {value:.0%}")
                elif kind == "tools_progress":
                    self.progress["value"] = value * 100
                    self.status.set(f"Downloading and checking video tools · {value:.0%}")
                elif kind == "tools_ready":
                    self.status.set("Video tools ready. Choose both folders, then click Scan for matches.")
                    if value and not self.closing: self.root.after(0, value)
                elif kind == "export_preview":
                    generation, destination, output_size = value
                    if generation == self.preview_generation and not self.closing and self.export_video():
                        with Image.open(destination) as frame:
                            self.preview_source = frame.convert("RGB")
                        self.preview_display_size = None
                        self.preview_title.set(f"Export preview · {output_size[0]} × {output_size[1]}")
                        self.resize_preview()
                elif kind == "preview_failed":
                    generation, message = value if isinstance(value, tuple) else (value, "Select the export video again to retry.")
                    if generation == self.preview_generation and self.export_video():
                        self.preview_source = None; self.preview_display_size = None
                        self.preview_request = None
                        self.preview_title.set("Export preview unavailable")
                        self.preview_label.configure(image="", text="Export preview unavailable.\n" + message[:180])
                elif kind == "scan":
                    self.scan = value
                    self.included_videos = {m.video.path.name for m in value.matches}
                    self.update_selection_summary()
                    self.refresh_rows()
                    for err in value.errors:
                        self.log("ISSUE: " + err)
                    for name in value.summary()["unmatched_vbo"]:
                        self.log(f"No GoPro overlap: {name}")
                    self.status.set("Ready. Check the preview before exporting." if value.matches else "No matches. See Options → Details.")
                elif kind == "complete":
                    self.result = value
                    self.status.set("Files created. Open the folder or view the report.")
                    self.open_result.configure(state="normal"); self.open_report.configure(state="normal")
                elif kind == "cancelled":
                    self.status.set("Cancelled. Original files are unchanged; diagnostics remain in the working folder.")
                elif kind == "error":
                    text, trace = value
                    self.log(text); self.status.set("Could not complete. See Options → Details.")
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
        self.preview_cancel.set()
        self.save_preferences()
        self.root.destroy()

    def save_preferences(self):
        try:
            self.preferences.parent.mkdir(parents=True, exist_ok=True)
            self.preferences.write_text(json.dumps({"folder": self.folder.get(), "quality": self.quality.get(),
                                                    "gopro_folder": "" if self._gopro_folder_automatic else self.gopro_folder.get(),
                                                    "include_gopro_subfolders": self.recursive_gopro.get(),
                                                    "overlay_mode": self.overlay_mode.get(), "scene_path": self.scene_path.get(),
                                                    "overlap_only": self.overlap_only.get(),
                                                    "audio_source": SOUND[self.audio_source.get()],
                                                    "time_basis": self.time_basis.get(),
                                                    "welcomed": self.welcomed}), encoding="utf-8")
        except OSError:
            pass


def launch(folder: Path | None = None, *, gopro_folder: Path | None = None,
           recursive_gopro: bool | None = None, scene_path: Path | None = None):
    root = tk.Tk()
    app = App(root, folder, gopro_folder=gopro_folder, recursive_gopro=recursive_gopro)
    if scene_path is not None:
        app.scene_path.set(str(scene_path))
        app.overlay_mode.set("Full scene · no rear camera")
        app.overlay_changed()
    root.mainloop()
