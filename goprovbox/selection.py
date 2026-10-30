"""Readable recording and lap information for choosing exports."""
from datetime import datetime, timezone
import math


TIME_BASES = ("Local time", "UTC")


def recording_datetime(stamp, basis="Local time", local_zone=None):
    value = datetime.fromtimestamp(stamp, timezone.utc)
    return value if basis == "UTC" else value.astimezone(local_zone)


def date_and_time(stamp, basis="Local time", local_zone=None):
    try:
        value = recording_datetime(stamp, basis, local_zone)
        return value.strftime("%Y-%m-%d"), value.strftime("%H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "—", "—"


def moment(stamp, basis="Local time", local_zone=None):
    try:
        value = recording_datetime(stamp, basis, local_zone)
        offset = value.strftime("%z")
        label = "UTC" if offset == "+0000" else "UTC" + offset[:3] + ":" + offset[3:]
        return value.strftime("%Y-%m-%d %H:%M:%S") + " (" + label + ")"
    except (TypeError, ValueError, OverflowError, OSError):
        return "Unavailable"


def lap_time(seconds):
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return "—"
    millis = round(seconds * 1000)
    return f"{millis // 60000}:{millis // 1000 % 60:02d}.{millis % 1000:03d}"


def row_values(video_name, info, included, basis="Local time"):
    date, start = date_and_time(info["utc_start"], basis)
    best = info.get("fastest_lap")
    best_text = lap_time(best["duration_seconds"] if best else None)
    return ("☑" if included else "☐" if info["run_names"] else "—", video_name,
            ", ".join(info["run_names"]) or "No match", date, start, best_text,
            f"{info['overlap_seconds'] / 60:.2f} min")


def recording_details(video_name, info, basis="Local time"):
    lines = [video_name, "GoPro recording start: " + moment(info["utc_start"], basis),
             "GoPro recording end: " + moment(info["utc_end"], basis), "",
             "Best in file is the fastest complete lap entirely inside this GoPro file,",
             "using its GPS timestamps and the VBOX start/finish crossings.",
             "Laps split across files are excluded from this file's best time."]
    file_best = info.get("fastest_lap")
    if file_best:
        lines += ["", f"Best in file: {lap_time(file_best['duration_seconds'])} · lap {file_best['number']}",
                  "  VBOX source: " + info["fastest_run"],
                  "  Lap start: " + moment(file_best["utc_start"], basis),
                  "  Lap end: " + moment(file_best["utc_end"], basis),
                  f"  Complete laps in this file: {info['complete_laps']}"]
    elif info["runs"]:
        lines += ["", "No complete lap in this file: " + info.get("unavailable_reason", "Lap timing unavailable.")]
    lines += ["", "The red ★ separately marks footage from a VBOX run's fastest lap.",
              "If that lap spans files, keep all listed files for the complete lap."]
    if not info["runs"]:
        lines += ["", "No matching VBOX run."]
    for run in info["runs"]:
        lines += ["", run["name"], "  Run start: " + moment(run["utc_start"], basis),
                  "  Run end: " + moment(run["utc_end"], basis)]
        best = run.get("fastest_lap")
        if best:
            lines += [f"  Whole-run best: {lap_time(best['duration_seconds'])} · lap {best['number']}",
                      "  Lap start: " + moment(best["utc_start"], basis),
                      "  Lap end: " + moment(best["utc_end"], basis)]
            chapters = run["fastest_lap_videos"]
            lines.append("  Whole-run fastest-lap footage: " + (", ".join(chapters) if chapters else "No matching GoPro footage"))
            if run["contains_fastest"]:
                lines.append("  Red ★: this video contains footage from the whole-run best lap.")
                if info["utc_start"] > best["utc_start"] or info["utc_end"] < best["utc_end"]:
                    lines.append("  This video contains only part of that lap.")
        else:
            lines.append("  Best lap unavailable: " + (run.get("unavailable_reason") or "No complete lap"))
    return "\n".join(lines)
