"""Small run summaries for choosing footage before a long conversion."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from threading import Event
import re

from .gpmf import TelemetryError
from .lapdata import Session


def summarize_run(vbo) -> dict:
    """Reuse the renderer's complete-lap rules, retaining numeric UTC times."""
    result = {"name": vbo.path.name, "path": str(vbo.path),
              "utc_start": vbo.rows[0].utc if vbo.rows else None,
              "utc_end": vbo.rows[-1].utc if vbo.rows else None,
              "complete_laps": 0, "laps": [], "fastest_lap": None, "unavailable_reason": ""}
    if not any(re.match(r"^Start\s", line, re.I) for line in vbo.preamble):
        result["unavailable_reason"] = "No start/finish line is recorded."
        return result
    try:
        session = Session(vbo)
        result["complete_laps"] = len(session.laps)
        result["laps"] = [{"number": lap.number, "duration_seconds": lap.duration,
                           "utc_start": lap.start, "utc_end": lap.end} for lap in session.laps]
        if session.laps:
            result["fastest_lap"] = min(result["laps"], key=lambda lap: (
                lap["duration_seconds"], lap["utc_start"], lap["number"]))
        elif len(session.crossings) < 2:
            result["unavailable_reason"] = "No complete lap between valid start/finish crossings."
        else:
            result["unavailable_reason"] = "No complete lap without a telemetry gap was found."
    except (TelemetryError, OSError, ValueError, KeyError, IndexError, TypeError, ArithmeticError) as error:
        # Matching and uncropped conversion can remain useful without lap timing.
        result["unavailable_reason"] = "Lap timing is unavailable: " + str(error)
    return result


def collect_run_info(vbos, log=lambda message: None, cancel: Event | None = None) -> dict[str, dict]:
    """Calculate each full VBOX run once in the scan worker, not in the UI."""
    result = {}
    for vbo in vbos:
        if cancel and cancel.is_set():
            raise InterruptedError("Cancelled")
        log(f"Calculating complete laps for {vbo.path.name}")
        result[vbo.path.name] = summarize_run(vbo)
        if cancel and cancel.is_set():
            raise InterruptedError("Cancelled")
    return result


def _covers_lap(match, lap) -> bool:
    if not match.rows:
        return False
    start = max(match.rows[0].utc, match.video.clock.utc(0), lap["utc_start"])
    end = min(match.rows[-1].utc, match.video.clock.utc(match.video.duration), lap["utc_end"])
    return end > start


def _complete_laps_in_video(vbo, matches, video, laps) -> list[dict]:
    """Require the whole lap and its available bracketing data in this file.

    Crossings are interpolated between VBOX samples. At a camera boundary the
    first/last matched sample can therefore sit just inside the complete lap.
    Samples outside the file are not required; all lap/bracketing samples that
    do occur inside it must have verified matching coverage.
    """
    if not laps:
        return []
    start, end = video.clock.utc(0), video.clock.utc(video.duration)
    times = [row.utc for row in vbo.rows]
    covered = {row.utc for match in matches for row in match.rows}
    result = []
    for lap in laps:
        if lap["utc_start"] < start - 1e-6 or lap["utc_end"] > end + 1e-6:
            continue
        first = max(0, bisect_right(times, lap["utc_start"]) - 1)
        stop = min(len(times), bisect_left(times, lap["utc_end"]) + 1)
        required = [stamp for stamp in times[first:stop] if start <= stamp < end]
        if required and all(stamp in covered for stamp in required):
            result.append(dict(lap))
    return result


def video_run_info(scan, video) -> dict:
    """Show the best complete lap in this file, retaining whole-run markers.

    A run's fastest lap can straddle camera chapters. Each such chapter keeps
    its whole-run-best footage marker, independently of whether it contains a
    complete lap suitable for the per-file best-time column.
    """
    linked = [match for match in scan.matches if match.video is video]
    runs = []
    seen = set()
    for match in linked:
        name = match.vbo.path.name
        if name in seen:
            continue
        seen.add(name)
        stored = scan.run_info.get(name)
        if stored is None:
            stored = {"name": name, "path": str(match.vbo.path),
                      "utc_start": match.vbo.rows[0].utc if match.vbo.rows else None,
                      "utc_end": match.vbo.rows[-1].utc if match.vbo.rows else None,
                      "complete_laps": 0, "fastest_lap": None,
                      "unavailable_reason": "Lap timing was not calculated for this scan."}
        summary = dict(stored)
        lap = dict(summary["fastest_lap"]) if summary["fastest_lap"] is not None else None
        summary["fastest_lap"] = lap
        related = [candidate for candidate in scan.matches if candidate.vbo is match.vbo]
        summary["contains_fastest"] = bool(lap and any(
            candidate.video is video and _covers_lap(candidate, lap) for candidate in related))
        summary["fastest_lap_videos"] = sorted({candidate.video.path.name for candidate in related
                                                if lap and _covers_lap(candidate, lap)})
        laps = summary.get("laps")
        if laps is None:
            contained = []
            reason = "Complete-lap details were not calculated for this scan."
        else:
            summary["laps"] = [dict(item) for item in laps]
            contained = _complete_laps_in_video(match.vbo,
                [candidate for candidate in related if candidate.video is video], video, laps)
            reason = "" if contained else (summary.get("unavailable_reason") if not laps else "")
            reason = reason or "No complete lap with matching VBOX data is contained in this GoPro file."
        summary["complete_laps_in_video"] = len(contained)
        summary["best_in_video"] = min(contained, key=lambda item: (
            item["duration_seconds"], item["utc_start"], item["number"])) if contained else None
        summary["unavailable_reason_in_video"] = "" if contained else reason
        runs.append(summary)
    available = [run for run in runs if run["best_in_video"] is not None]
    best = min(available, key=lambda run: (run["best_in_video"]["duration_seconds"],
                                          run["best_in_video"]["utc_start"], run["name"])) if available else None
    reason = ""
    if not best:
        reason = (runs[0]["unavailable_reason_in_video"] if len(runs) == 1
                  else "No complete matched lap is contained in this GoPro file." if runs
                  else "No matching VBOX run.")
    return {"utc_start": video.clock.utc(0), "utc_end": video.clock.utc(video.duration),
            "overlap_seconds": sum(match.rows[-1].utc - match.rows[0].utc for match in linked if match.rows),
            "run_names": [run["name"] for run in runs], "runs": runs,
            "fastest_lap": best["best_in_video"] if best else None,
            "fastest_run": best["name"] if best else None,
            "complete_laps": sum(run["complete_laps_in_video"] for run in runs),
            "unavailable_reason": reason,
            "contains_fastest": any(run["contains_fastest"] for run in runs)}
