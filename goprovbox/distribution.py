"""Offline resources and an explicit, hash-pinned download of video tools."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

REPOSITORY = "https://github.com/wgm20/gopro-vbox-sync"
RELEASES = REPOSITORY + "/releases"
DOWNLOAD_PAGE = "https://wgm20.github.io/gopro-vbox-sync/"
FFMPEG_VERSION = "8.1.2"
FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/"
              "ffmpeg-8.1.2-essentials_build.zip")
# Matches the SHA-256 digest published by the distributor's GitHub release API.
FFMPEG_SHA256 = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
FFMPEG_BYTES = 109728040


def uninstall_command() -> list[str] | None:
    """Use only this installed copy's uninstaller, never another installation."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if executable.name.lower() != "goprovboxsync.exe":
        return None
    program = executable.parent / "unins000.exe"
    if (program.is_file() and program.with_suffix(".dat").is_file()
            and program.resolve().parent == executable.parent):
        # Keep the uninstaller's normal confirmation; never reboot automatically.
        return [str(program), "/NORESTART"]
    return None


def data_directory() -> Path:
    return Path(os.environ.get("GOPROVBOX_DATA_DIR") or
                str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GoProVBOXSync"))


def asset(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def tools_directory() -> Path:
    return data_directory() / "tools" / ("ffmpeg-" + FFMPEG_VERSION)


def tools_ready() -> bool:
    from .media import executable
    try:
        executable("ffmpeg"); executable("ffprobe")
        return True
    except ValueError:
        return False


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Video tools setup cancelled")


def install_archive(archive: Path, destination: Path, cancel=None):
    """Validate before writing; extract only known executable and licence files."""
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        while block := source.read(1024 * 1024):
            _cancelled(cancel); digest.update(block)
    if digest.hexdigest() != FFMPEG_SHA256:
        raise ValueError("Video tools download failed its integrity check. Please try again.")
    members = {"bin/ffmpeg.exe": "ffmpeg.exe", "bin/ffprobe.exe": "ffprobe.exe",
               "LICENSE": "LICENSE.txt", "README.txt": "README.txt"}
    prefix = f"ffmpeg-{FFMPEG_VERSION}-essentials_build/"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".video-tools-", dir=destination.parent) as tmp:
        stage = Path(tmp) / "ready"; stage.mkdir()
        with zipfile.ZipFile(archive) as package:
            for source, target in members.items():
                _cancelled(cancel)
                info = package.getinfo(prefix + source)
                if info.file_size > 300 * 1024 * 1024:
                    raise ValueError("Unexpected video tools archive size")
                with package.open(info) as src, (stage / target).open("xb") as dst:
                    shutil.copyfileobj(src, dst)
        # Rename does not replace an existing installation. A concurrent setup
        # cannot leave a partially published pair of executables.
        _cancelled(cancel)
        stage.rename(destination)


def download_tools(progress=lambda value: None, cancel=None) -> Path:
    destination = tools_directory()
    if all((destination / (name + ".exe")).is_file() for name in ("ffmpeg", "ffprobe")):
        return destination
    if destination.exists():
        raise ValueError(f"Incomplete video tools folder: {destination}. Rename it before retrying setup.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=destination.parent) as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        request = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": "GoPro-VBOX-Sync"})
        with urllib.request.urlopen(request, timeout=30) as response, archive.open("xb") as output:
            if not response.url.startswith("https://"):
                raise ValueError("Video tools download requires HTTPS")
            size = 0
            while block := response.read(1024 * 1024):
                _cancelled(cancel); size += len(block)
                if size > FFMPEG_BYTES:
                    raise ValueError("Video tools download is larger than expected")
                output.write(block); progress(.9 * size / FFMPEG_BYTES)
        if size != FFMPEG_BYTES:
            raise ValueError("Video tools download was incomplete. Please try again.")
        install_archive(archive, destination, cancel)
    progress(1)
    return destination


def copy_demo() -> Path:
    """Give each practice session its own writable folder, without overwriting."""
    parent = data_directory() / "Practice recordings"; parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="Demo-", dir=parent))
    for path in asset("demo").iterdir():
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    return destination
