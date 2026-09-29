"""Keep export diagnostics out of the folder opened in Circuit Tools."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import time
import uuid

from .distribution import data_directory


def records_directory(output: Path) -> Path:
    identity = os.path.normcase(str(output.resolve()))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return data_directory() / "Export reports" / key


def export_report_path(output: Path, name="Report.html") -> Path:
    """Locate a completed export's report, including pre-1.6.0b4 exports."""
    output = output.resolve()
    candidates = [output / "report.json"]
    candidates += sorted(records_directory(output).glob("*/report.json"), reverse=True)
    for candidate in candidates:
        try:
            report = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("status") != "complete":
                continue
            if candidate.parent != output and report.get("output_folder") != str(output):
                continue
            path = candidate.parent / name
            if path.is_file():
                return path
        except (OSError, ValueError):
            continue
    raise FileNotFoundError("The export report is no longer available on this computer")


def supporting_files(folder: Path, report: dict) -> list[Path]:
    """Only app-owned filenames are eligible; never sweep a user's folder."""
    names = {"report.json", "Report.html", "OPEN IN CIRCUIT TOOLS.txt"}
    for media in report["media"]:
        name = media["file"]
        preview = media.get("preview", Path(name).stem + ".jpg")
        if any(Path(n).name != n or "/" in n or "\\" in n for n in (name, preview)):
            raise ValueError("Export report contains an invalid filename")
        if Path(name).suffix.lower() != ".mp4" or Path(preview).suffix.lower() != ".jpg":
            raise ValueError("Export report contains an unexpected file type")
        names.add(preview)
        names.update(Path(name).stem + suffix for suffix in
                     (".encoding.log", ".audio.log", ".audio.audio.log", ".mux.log", ".audio-mux.log"))
    return [folder / name for name in sorted(names) if (folder / name).is_file()]


def archive_details(folder: Path, output: Path, report: dict, write_html) -> Path:
    """Copy and verify support files before removing them from the result."""
    sources = supporting_files(folder, report)
    record = records_directory(output) / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    record.mkdir(parents=True)
    saved = report | {"output_folder": str(output.resolve())}
    try:
        for source in sources:
            destination = record / source.name
            shutil.copy2(source, destination)
            if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
                raise OSError("Could not verify the saved export diagnostics")
        # Links to VBOs now point back to the finished folder; previews remain
        # beside the report. Retain a copy of any older HTML/JSON before updating.
        for name in ("Report.html", "report.json"):
            original = record / name
            if original.exists():
                original.rename(record / ("previous-" + name))
        write_html(saved, record / "Report.html")
        (record / "report.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
        for source in sources:
            source.unlink()
        return record
    except BaseException as exc:
        # An incomplete archive must never be mistaken for a completed export.
        saved.update(status="failed", failure=str(exc))
        try:
            (record / "report.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
        except OSError:
            pass
        raise
