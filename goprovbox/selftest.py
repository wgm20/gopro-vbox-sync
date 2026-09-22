"""Exercise the installed app with packaged, synthetic input only."""
from pathlib import Path
import json
import shutil
import sys
import tkinter as tk

from . import __version__
from .distribution import asset, uninstall_command
from .engine import scan_folder, export
from .media import probe, run, executable
from .vbo import read_vbo


def run_self_test(destination: Path):
    destination.mkdir(parents=True, exist_ok=False)
    root = tk.Tk(); root.withdraw()
    icon = tk.PhotoImage(file=str(asset("icon.png")))
    root.iconphoto(True, icon); root.update(); root.destroy()
    source = destination / "Practice recordings"
    shutil.copytree(asset("demo"), source)
    # Synthetic original-recorder video supplies a different display shape so
    # the frozen-app check also exercises reference discovery and real cropping.
    run([executable("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=black:s=640x320:r=30",
         "-t", "1", "-c:v", "libx264", str(source / "VBOX0001_0001.mp4")])
    scan = scan_folder(source)
    assert not scan.errors, scan.errors
    assert len(scan.matches) == len(scan.videos) == 2
    assert all(v.orientation.clockwise == 0 for v in scan.videos)
    assert [len(m.rows) for m in scan.matches] == [100, 101]
    chosen = scan.videos[0].path.name
    result = export(scan, destination / "Export", include_videos={chosen},
                    encoder="software", telemetry_overlay=True, crops={chosen: "centre"})
    videos = list(result.glob("*.mp4"))
    assert len(videos) == 1
    assert len(list(result.glob("*.vbo"))) == 1
    assert probe(videos[0])["streams"][0]["width"] == 640
    assert probe(videos[0])["streams"][0]["height"] == 320
    exported = json.loads((result / "report.json").read_text(encoding="utf-8"))
    assert exported["settings"]["overlap_only"]
    assert exported["media"][0]["range"]["source_start_seconds"] == 2
    assert exported["media"][0]["verification"]["frames"] == "300"
    linked = read_vbo(result / exported["outputs"][0]["file"])
    assert int(linked.rows[0].values[linked.index("avitime")]) == 0
    assert (result / "Report.html").is_file()
    assert asset("quick-start.html").is_file()
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "uninstaller_available": uninstall_command() is not None,
              "passed": True, "matched_videos": 2, "exported_videos": 1,
              "checks": ["Tk and packaged icon", "GPS timing", "upright metadata", "partial overlaps",
                         "selected video only", "overlap-only trim and rebased VBOX times", "VBOX-shaped centre crop", "four-channel overlay", "encoded video and VBO verification",
                         "offline help"],
              "limits": "Does not test Circuit Tools acceptance or a fresh Windows machine."}
    (destination / "self-test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
