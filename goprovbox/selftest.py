"""Exercise the installed app with packaged, synthetic input only."""
from pathlib import Path
from threading import Event
import json
import shutil
import sys
import time
import tkinter as tk

from . import __version__
from .distribution import asset, uninstall_command
from .engine import scan_folder, export
from .reports import export_report_path
from .media import probe, run, executable
from .vbo import read_vbo, write_vbo


def run_self_test(destination: Path, *, scene_path: Path | None = None):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    root = tk.Tk(); root.withdraw()
    icon = tk.PhotoImage(file=str(asset("icon.png")))
    root.iconphoto(True, icon); root.update(); root.destroy()
    source = destination / "Practice recordings"
    shutil.copytree(asset("demo"), source)
    card = destination / "SD card"
    dcim = card / "DCIM" / "100GOPRO"
    dcim.mkdir(parents=True)
    for path in source.glob("*.MP4"):
        path.rename(dcim / path.name)
    if scene_path is not None:
        # This is a copied synthetic practice fixture, never a user's VBO.
        # The saved test scene also displays a CAN acceleration channel.
        practice = read_vbo(source / "VBOX0001.vbo")
        practice.columns.append("acceleration")
        practice.preamble.insert(practice.preamble.index("[channel units]") - 1, "Acceleration")
        practice.preamble.insert(practice.preamble.index("[comments]") - 1, "g")
        practice.preamble[practice.preamble.index("[column names]") + 1] += " Acceleration"
        for row in practice.rows:
            row.values.append("0")
        write_vbo(practice, practice.rows,
                  [float(row.values[practice.index("avitime")]) / 1000 for row in practice.rows],
                  "VBOX0001_", practice.path,
                  [int(row.values[practice.index("avifileindex")]) for row in practice.rows])
    # Synthetic original-recorder video supplies a different display shape so
    # the frozen-app check exercises cropping and replacement of GoPro sound.
    run([executable("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=black:s=640x320:r=30",
         "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*(200*t+20*t*t)):s=48000",
         "-t", "24", "-c:v", "libx264", "-c:a", "alac", str(source / "VBOX0001_0001.mp4")])
    scan = scan_folder(source, gopro_folder=card, recursive_gopro=True)
    assert not scan.errors, scan.errors
    assert len(scan.matches) == len(scan.videos) == 2
    assert all(v.path.parent == dcim for v in scan.videos)
    assert all(scan.source_path(v.path.name) == v.path for v in scan.videos)
    assert all(v.orientation.clockwise == 0 for v in scan.videos)
    assert [len(m.rows) for m in scan.matches] == [100, 101]
    chosen = scan.videos[0].path.name
    from .gui import App
    root = tk.Tk(); root.withdraw()
    app = App(root, source, gopro_folder=card)
    assert app.options_menu.type("Include GoPro subfolders") == "checkbutton"
    assert app.recursive_gopro.get()
    assert not hasattr(app, "quality_combo")
    custom = {"mode": "custom", "x": .5, "y": .75}
    app.crops = {chosen: custom.copy()}
    app.overlay_mode.set("Full scene · no rear camera" if scene_path else "Driving data")
    if scene_path:
        app.scene_path.set(str(scene_path))
    app.overlay_changed()
    app.scan = scan; app.included_videos = {chosen}
    app.time_basis.set("UTC")
    assert app.tree.set("0", "date") == "2026-01-01"
    assert app.tree.set("0", "start") == "12:00:00"
    assert app.tree.set("0", "lap") == "—"  # The practice data has no start line.
    assert app.tree.heading("lap", "text") == "Best in file"
    assert not app.tree.item("0", "image")
    assert app.tree.get_children()[0] == "0"
    app.sort_rows("start", True)
    assert app.tree.get_children()[0] == "1"
    assert app.included_videos == {chosen}
    deadline = time.monotonic() + 30
    while app.preview_source is None and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    assert app.preview_source is not None, "The composed export preview did not finish"
    assert app.export_video().path.name == chosen
    assert tuple(app.export_video_combo.cget("values")) == (chosen,)
    assert app.preview_source.size == (640, 320)
    assert app.preview_title.get() == "Export preview · 640 × 320"
    app.preview_cancel.set()
    for callback in root.tk.call("after", "info"):
        root.after_cancel(callback)
    root.destroy(); app.temporary.cleanup()
    # Exercise the packaged app at the reported long-path boundary, including
    # WAV preparation, muxing, logs, previews and the final folder rename.
    output_parent = (destination / "Race day recordings").resolve()
    output_parent /= "x" * max(1, 182 - len(str(output_parent)) - 1)
    output = output_parent / "GoPro Circuit Tools Overlay Cropped Matched"
    legacy_audio = output_parent / ("." + output.name + ".working-12345678") / "GoPro_GX01D001_0001.audio.wav"
    assert len(str(legacy_audio)) >= 260
    result = export(scan, output, include_videos={chosen},
                    encoder="software", telemetry_overlay=True, crops={chosen: custom},
                    overlay_scene=scene_path, overlay_mode="full" if scene_path else "four")
    for path in result.iterdir():
        assert len(str(path)) < 260, path.name
        with path.open("rb") as handle:
            handle.read(1)
    videos = list(result.glob("*.mp4"))
    assert all(p.suffix in (".mp4", ".vbo") for p in result.iterdir())
    assert len(videos) == 1
    assert len(list(result.glob("*.vbo"))) == 1
    assert probe(videos[0])["streams"][0]["width"] == 640
    assert probe(videos[0])["streams"][0]["height"] == 320
    exported = json.loads(export_report_path(result, "report.json").read_text(encoding="utf-8"))
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
                               0, 1920, scan.crop_references[chosen].aspect,
                               "full" if scene_path else "four", str(scene_path) if scene_path else "",
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
    assert export_report_path(result).is_file()
    assert asset("quick-start.html").is_file()
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "uninstaller_available": uninstall_command() is not None,
              "passed": True, "matched_videos": 2, "exported_videos": 1, "export_folder": str(result),
              "report_file": str(export_report_path(result)),
              "checks": ["Tk and packaged icon", "recording date/time/per-file best-lap selection table",
                         "checked-file-only cropped and composited export preview",
                         "separate GoPro SD card with nested DCIM folder", "GPS timing", "upright metadata", "partial overlaps",
                         "selected video only", "overlap-only trim and rebased VBOX times", "custom VBOX crop and interactive editor",
                         ("full saved scene overlay" if scene_path.suffix.lower() == ".gvscene"
                          else "full VVHSN scene import and overlay") if scene_path else "four-channel overlay",
                         "encoded video and VBO verification",
                         "VBOX sound and decoded alignment at both ends", "long output folder with short temporary paths",
                         "MP4 and VBO only in output; report stored separately", "offline help"],
              "limits": "Does not test Circuit Tools acceptance or a fresh Windows machine."}
    (destination / "self-test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
