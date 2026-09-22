from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from .engine import scan_folder, export
from .gpmf import TelemetryError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Match GoPro video to VBOX telemetry using GPS UTC; originals are never overwritten.")
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("--scan", action="store_true", help="Inspect and report only; no video conversion")
    parser.add_argument("--gui", action="store_true", help="Open the desktop interface")
    parser.add_argument("--output", type=Path, help="New output folder (default: GoPro Circuit Tools inside source folder)")
    parser.add_argument("--report", type=Path, help="Save scan report as JSON")
    parser.add_argument("--max-size", type=int, default=1920, help="Video longest edge; default 1920, 0 retains original resolution")
    parser.add_argument("--encoder", choices=["auto", "qsv", "software"], default="auto")
    parser.add_argument("--rotate", action="append", default=[], metavar="FILENAME=DEGREES", help="Override rotation, clockwise 0/90/180/270; repeat per file")
    parser.add_argument("--crop", choices=["none", "top", "bottom", "centre"], default="none",
                        help="Match the original VBOX video's shape: remove top, bottom, or centre crop")
    parser.add_argument("--full-video", action="store_true", help="Keep full videos instead of only periods with matching VBOX data")
    parser.add_argument("--overlay", action="store_true", help="Burn speed, RPM, throttle and brake into encoded video")
    parser.add_argument("--scene", type=Path, help="VBOX HD2 VVHSN scene (implies --overlay)")
    parser.add_argument("--overlay-mode", choices=["four", "full"], default="four", help="four driving channels, or full scene without the rear camera")
    args = parser.parse_args(argv)
    if args.gui or args.folder is None:
        from .gui import launch
        launch(args.folder)
        return 0
    try:
        rotations = {}
        for item in args.rotate:
            name, value = item.rsplit("=", 1)
            rotations[name] = int(value)
        scan = scan_folder(args.folder, log=print)
        for match in scan.matches:
            print(f"MATCH: {match.video.path.name} + {match.vbo.path.name}: {len(match.rows):,} samples, {match.rows[-1].utc - match.rows[0].utc:.2f}s")
        for error in scan.errors:
            print("ISSUE: " + error, file=sys.stderr)
        if args.report:
            if args.report.resolve() in {(scan.folder / name).resolve() for name in scan.fingerprints}:
                raise TelemetryError("The scan report must not overwrite an original recording")
            # Exclusive creation also protects files skipped by the scanner.
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(scan.summary(), handle, indent=2)
        if args.scan:
            return 0 if scan.matches else 2
        last = [-1]
        def progress(value):
            p = int(value * 100)
            if p != last[0]:
                print(f"Progress: {p}%", flush=True); last[0] = p
        export(scan, args.output, rotations=rotations, max_size=args.max_size, encoder=args.encoder,
               crops={v.path.name: args.crop for v in scan.videos},
               overlap_only=not args.full_video,
               log=print, progress=progress, telemetry_overlay=args.overlay, overlay_scene=args.scene, overlay_mode=args.overlay_mode)
        return 0
    except (KeyboardInterrupt, InterruptedError):
        print("Cancelled. Original recordings are unchanged.", file=sys.stderr)
        return 130
    except (TelemetryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
