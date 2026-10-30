"""Use the installed editor's public scene importer in a separate .NET host."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

from .distribution import asset, data_directory
from .gpmf import TelemetryError
from .media import CREATE_NO_WINDOW

MAX_SIZE = 32 * 1024 * 1024
MAX_ENTRIES = 2048
RUNTIME_CONFIG = ('{"runtimeOptions":{"tfm":"net8.0","framework":'
                  '{"name":"Microsoft.WindowsDesktop.App","version":"8.0.0"},'
                  '"rollForward":"LatestMinor"}}\n')


def library_directory() -> Path:
    candidates = [os.environ.get("GOPROVBOX_VBOX_VIDEO")]
    candidates += [str(Path(os.environ.get(variable, fallback)) / "Racelogic" / "VBOX Video")
                   for variable, fallback in (("ProgramFiles(x86)", "C:/Program Files (x86)"),
                                              ("ProgramFiles", "C:/Program Files"))]
    for candidate in candidates:
        if candidate and (Path(candidate) / "VBoxVideo.FileRoutines.dll").is_file():
            return Path(candidate).resolve()
    raise TelemetryError("Install VBOX Video Setup to import .VVHSN scenes, or choose a saved .gvscene file")


def find_dotnet() -> Path:
    candidates = [os.environ.get("GOPROVBOX_DOTNET")]
    candidates += [str(Path(os.environ[root]) / "dotnet.exe")
                   for root in ("DOTNET_ROOT_X64", "DOTNET_ROOT") if os.environ.get(root)]
    candidates += [str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet" / "dotnet.exe"),
                   shutil.which("dotnet")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise TelemetryError("Install or repair VBOX Video Setup and its .NET 8 Desktop Runtime to import .VVHSN scenes")


def build_helper(destination: Path) -> Path:
    """Build our small host using the C# compiler included with Windows."""
    source = Path(__file__).with_suffix(".cs")
    if not source.is_file():
        raise TelemetryError("The scene import helper source is missing. Reinstall GoPro VBOX Sync")
    identity = hashlib.sha256(source.read_bytes() + RUNTIME_CONFIG.encode()).hexdigest()
    target = destination / "scene-reader.dll"
    config = destination / "scene-reader.runtimeconfig.json"
    marker = destination / ".scene-reader-build.json"
    def current():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            return (saved.get("source") == identity and config.read_text(encoding="utf-8") == RUNTIME_CONFIG
                    and saved.get("helper") == hashlib.sha256(target.read_bytes()).hexdigest())
        except (OSError, ValueError, AttributeError):
            return False
    if current():
        return target
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    compiler = next((windows / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe"
                     for framework in ("Framework64", "Framework")
                     if (windows / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe").is_file()), None)
    if compiler is None:
        raise TelemetryError("The scene import helper is missing. Reinstall GoPro VBOX Sync or build its scene helper on Windows")
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".scene-build-", dir=destination) as temporary:
        compiled = Path(temporary) / "scene-reader.dll"
        result = subprocess.run([str(compiler), "/nologo", "/target:exe", "/optimize+",
                                 "/out:" + str(compiled), str(source)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=60, creationflags=CREATE_NO_WINDOW)
        if result.returncode or not compiled.is_file():
            raise TelemetryError("Could not build the scene import helper: " + (result.stderr or result.stdout)[-1200:].strip())
        # Another first-use build may have published this same helper while
        # the compiler ran. Avoid replacing a DLL already in use by .NET.
        if current():
            return target
        helper_digest = hashlib.sha256(compiled.read_bytes()).hexdigest()
        config.write_text(RUNTIME_CONFIG, encoding="utf-8")
        compiled.replace(target)
        saved = Path(temporary) / "build.json"
        saved.write_text(json.dumps({"source": identity, "helper": helper_digest}), encoding="utf-8")
        saved.replace(marker)
    return target


def helper_path() -> Path:
    bundled = asset("scene-reader.dll")
    if bundled.is_file() and bundled.with_name("scene-reader.runtimeconfig.json").is_file():
        return bundled
    if getattr(sys, "frozen", False):
        raise TelemetryError("The scene import helper is missing. Reinstall GoPro VBOX Sync")
    source = Path(__file__).with_suffix(".cs")
    if not source.is_file():
        raise TelemetryError("The scene import helper source is missing. Reinstall GoPro VBOX Sync")
    identity = hashlib.sha256(source.read_bytes() + RUNTIME_CONFIG.encode()).hexdigest()[:16]
    folder = data_directory() / "scene-reader" / identity
    helper = folder / "scene-reader.dll"
    if not helper.is_file() or not helper.with_name("scene-reader.runtimeconfig.json").is_file():
        return build_helper(folder)
    return helper


def directory_members(folder: Path, max_size=MAX_SIZE, max_entries=MAX_ENTRIES) -> dict[str, bytes]:
    """Read only regular, bounded scene files inside the helper's output folder."""
    root_info = folder.lstat()
    if stat.S_ISLNK(root_info.st_mode) or getattr(root_info, "st_reparse_tag", 0) == 0xA0000003:
        raise TelemetryError("Scene import contains an unsafe linked output folder")
    base = folder.resolve()
    result, total = {}, 0
    pending = [folder]
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir()):
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) == 0xA0000003
                    or not path.resolve().is_relative_to(base)):
                raise TelemetryError("Scene import contains an unsafe linked file or folder")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                if getattr(info, "st_nlink", 1) > 1:
                    raise TelemetryError("Scene import contains an unsafe linked file")
                total += info.st_size
                if total > max_size or len(result) >= max_entries:
                    raise TelemetryError("Expanded scene exceeds the size or file-count limit")
                name = path.relative_to(folder).as_posix()
                data = path.read_bytes()
                if len(data) != info.st_size:
                    raise TelemetryError("Scene import changed while being read")
                result[name] = data
            else:
                raise TelemetryError("Scene import contains an unsupported file type")
    missing = sorted({"SCENE.XML", "VBOXHD.XML"} - result.keys())
    if missing:
        raise TelemetryError("Imported scene is missing " + ", ".join(missing))
    return result


def read_scene_files(path: Path, max_size=MAX_SIZE, max_entries=MAX_ENTRIES) -> dict[str, bytes]:
    if path.stat().st_size > max_size:
        raise TelemetryError("Scene file exceeds the size limit")
    library = library_directory()
    dotnet, helper = find_dotnet(), helper_path()
    with TemporaryDirectory(prefix="gvs-scene-") as temporary:
        output = Path(temporary) / "imported"
        output.mkdir()
        try:
            result = subprocess.run([str(dotnet), str(helper), str(library), str(path.resolve()), str(output)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=60, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise TelemetryError("Reading the VBOX scene timed out") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            if "Microsoft.WindowsDesktop.App" in detail or "must install or update .NET" in detail:
                raise TelemetryError("Install or repair VBOX Video Setup and its .NET 8 Desktop Runtime to import this scene")
            raise TelemetryError("VBOX scene import failed: " + (detail[-1200:] or "the installed importer returned an error"))
        returned = result.stdout.strip()
        if not returned or "\n" in returned or "\r" in returned:
            raise TelemetryError("VBOX scene importer did not return a valid output folder")
        folder = Path(returned)
        if not folder.is_absolute() or not folder.resolve().is_relative_to(output.resolve()):
            raise TelemetryError("VBOX scene importer returned a folder outside its temporary output")
        if not folder.is_dir():
            raise TelemetryError("VBOX scene importer did not create its output folder")
        return directory_members(folder, max_size, max_entries)
