"""Exercise the installed app with packaged, synthetic input only."""
from pathlib import Path
from threading import Event
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
    # the frozen-app check exercises cropping and replacement of GoPro sound.
    run([executable("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=black:s=640x320:r=30",
         "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*(200*t+20*t*t)):s=48000",
         "-t", "24", "-c:v", "libx264", "-c:a", "alac", str(source / "VBOX0001_0001.mp4")])
    scan = scan_folder(source)
    assert not scan.errors, scan.errors
    assert len(scan.matches) == len(scan.videos) == 2
    assert all(v.orientation.clockwise == 0 for v in scan.videos)
    assert [len(m.rows) for m in scan.matches] == [100, 101]
    chosen = scan.videos[0].path.name
    custom = {"mode": "custom", "x": .5, "y": .75}
    result = export(scan, destination / "Export", include_videos={chosen},
                    encoder="software", telemetry_overlay=True, crops={chosen: custom})
    videos = list(result.glob("*.mp4"))
    assert len(videos) == 1
    assert len(list(result.glob("*.vbo"))) == 1
    assert probe(videos[0])["streams"][0]["width"] == 640
    assert probe(videos[0])["streams"][0]["height"] == 320
    exported = json.loads((result / "report.json").read_text(encoding="utf-8"))
    assert exported["settings"]["overlap_only"]
    assert exported["media"][0]["range"]["source_start_seconds"] == 2
    assert exported["media"][0]["verification"]["frames"] == "300"
    assert exported["settings"]["crops"][chosen]["rectangle"]["y"] == 30
    sound = exported["media"][0]["audio"]
    assert sound["source"] == "vbox_preferred"
    assert {s["kind"] for s in sound["plan"]["segments"]} == {"vbox"}
    assert abs(sound["plan"]["segments"][0]["source_start_seconds"] - 2) < .002
    assert abs(sound["duration_seconds"] - 10) < .002
    from array import array
    import math
    def audio_samples(path, start):
        return array('f', run([executable("ffmpeg"), "-v", "error", "-i", str(path),
                              "-ss", str(start), "-t", "0.2", "-map", "0:a:0",
                              "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]))
    for point in (.4, 9.7):
        original = audio_samples(source / "VBOX0001_0001.mp4", point + 2)
        output = audio_samples(videos[0], point)
        correlation = sum(a*b for a, b in zip(original, output)) / math.sqrt(
            sum(a*a for a in original)*sum(b*b for b in output))
        assert correlation > .98, (point, correlation)
    from .framing import CropEditor, prepare_preview
    prepared = prepare_preview(scan.videos[0], [m for m in scan.matches if m.video.path.name == chosen],
                               0, 1920, scan.crop_references[chosen].aspect, "four", "",
                               destination / "framing.png", Event())
    root = tk.Tk(); root.withdraw()
    applied = []
    editor = CropEditor(root, prepared, custom, applied.append); editor.withdraw()
    editor.nudge(0, -1); editor.commit()
    assert prepared.crop(applied[0]).y == 28
    assert prepared.output(applied[0], (640, 320)).size == (640, 320)
    root.destroy()
    linked = read_vbo(result / exported["outputs"][0]["file"])
    assert int(linked.rows[0].values[linked.index("avitime")]) == 0
    assert (result / "Report.html").is_file()
    assert asset("quick-start.html").is_file()
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "uninstaller_available": uninstall_command() is not None,
              "passed": True, "matched_videos": 2, "exported_videos": 1,
              "checks": ["Tk and packaged icon", "GPS timing", "upright metadata", "partial overlaps",
                         "selected video only", "overlap-only trim and rebased VBOX times", "custom VBOX crop and interactive editor", "four-channel overlay", "encoded video and VBO verification",
                         "VBOX sound and decoded alignment at both ends", "offline help"],
              "limits": "Does not test Circuit Tools acceptance or a fresh Windows machine."}
    (destination / "self-test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
