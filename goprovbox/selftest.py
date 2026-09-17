"""Exercise the installed app with packaged, synthetic input only."""
from pathlib import Path
import json
import shutil
import sys
import tkinter as tk

from . import __version__
from .distribution import asset
from .engine import scan_folder, export
from .media import probe


def run_self_test(destination: Path):
    destination.mkdir(parents=True, exist_ok=False)
    root = tk.Tk(); root.withdraw()
    icon = tk.PhotoImage(file=str(asset("icon.png")))
    root.iconphoto(True, icon); root.update(); root.destroy()
    source = destination / "Practice recordings"
    shutil.copytree(asset("demo"), source)
    scan = scan_folder(source)
    assert not scan.errors, scan.errors
    assert len(scan.matches) == len(scan.videos) == 2
    assert all(v.orientation.clockwise == 0 for v in scan.videos)
    assert [len(m.rows) for m in scan.matches] == [100, 101]
    chosen = scan.videos[1].path.name
    result = export(scan, destination / "Export", include_videos={chosen},
                    encoder="software", telemetry_overlay=True)
    videos = list(result.glob("*.mp4"))
    assert len(videos) == 1
    assert len(list(result.glob("*.vbo"))) == 1
    assert probe(videos[0])["streams"][0]["width"] == 640
    assert (result / "Report.html").is_file()
    assert asset("quick-start.html").is_file()
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "passed": True, "matched_videos": 2, "exported_videos": 1,
              "checks": ["Tk and packaged icon", "GPS timing", "upright metadata", "partial overlaps",
                         "selected video only", "four-channel overlay", "encoded video and VBO verification",
                         "offline help"],
              "limits": "Does not test Circuit Tools acceptance or a fresh Windows machine."}
    (destination / "self-test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
