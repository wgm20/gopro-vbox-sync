"""Install into a new temporary directory; exercise the frozen app without PATH tools."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goprovbox import __version__
from goprovbox.distribution import tools_directory


def main():
    root = Path(__file__).resolve().parents[1]
    installer = root / "release-output" / f"GoPro-VBOX-Sync-{__version__}-Setup.exe"
    # Retain evidence for diagnosis. Each run uses a new path with spaces.
    evidence = Path(tempfile.mkdtemp(prefix="goprovbox install test "))
    target = evidence / "Installed app"
    result = subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
                             "/TASKS=", "/DIR=" + str(target), "/LOG=" + str(evidence / "install.log")], timeout=180)
    if result.returncode: raise RuntimeError(f"Installer failed: {result.returncode}")
    env = os.environ.copy()
    # No installed Python or FFmpeg may be found through PATH. Use only the
    # verified video tools that a real first-time user downloads through Help.
    for key in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(key, None)
    env["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
    for name in ("ffmpeg", "ffprobe"):
        env["GOPROVBOX_" + name.upper()] = str(tools_directory() / (name + ".exe"))
    env["GOPROVBOX_DATA_DIR"] = str(evidence / "Fresh settings")
    result = subprocess.run([str(target / "GoProVBOXSync.exe"), "--self-test", str(evidence / "self test")],
                            cwd=evidence, env=env, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Installed app test failed; inspect {evidence}")
    report = json.loads((evidence / "self test/self-test.json").read_text())
    assert report["passed"] and report["frozen"]
    # Verify the uninstaller removes only the installed app, preserving exports.
    result = subprocess.run([str(target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
    assert result.returncode == 0
    assert not (target / "GoProVBOXSync.exe").exists()
    assert (evidence / "self test/Export/Report.html").is_file()
    report["installer"] = "passed"; report["uninstaller"] = "passed, exports retained"
    (root / "release-output/validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Installer, isolated runtime and uninstall passed. Evidence: {evidence}")


if __name__ == "__main__": main()
