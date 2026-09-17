# Build and release

Use 64-bit Windows and Python **3.13.15** with Tkinter. Build on a clean checkout. The Windows installer includes Python and Pillow; users do not install either.

```powershell
python -m venv .venv-release
.\.venv-release\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\bootstrap_inno.ps1
.\.venv-release\Scripts\python.exe -c "from goprovbox.distribution import download_tools; download_tools()"
```

The bootstrap script downloads Inno Setup 6.7.3 to `.build-tools`, verifies its published hash and installs the compiler there. It does not bundle Inno Setup itself in the app. The video-tools setup downloads FFmpeg 8.1.2 from Gyan's release and verifies the release API's SHA-256 digest. To run every integration test, put the downloaded tools directory on PATH for the build session:

```powershell
$videoTools = .\.venv-release\Scripts\python.exe -c "from goprovbox.distribution import tools_directory; print(tools_directory())"
$env:PATH = "$videoTools;$env:PATH"
.\.venv-release\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-release\Scripts\python.exe scripts\build_release.py
.\.venv-release\Scripts\python.exe scripts\test_installer.py
```

`test_installer.py` installs the actual release into a new temporary folder, removes Python and FFmpeg from the child process's PATH, scans the packaged demo and exports one selected video with gauges. It checks frozen execution, then uninstalls and verifies that the export survived. Run this on a disposable build machine where GoPro VBOX Sync is not already installed: the installer uses the real application's uninstall registration. Evidence remains in the temporary folder; a summary is written to `release-output/validation.json`.

This is an isolated-runtime test, not a claim of a completely clean Windows OS. Before removing the beta label, also test installation on fresh Windows 10 and 11 machines and resolve the Circuit Tools authenticity issue using original recordings. Synthetic data cannot establish recorder authenticity.

Build outputs in `release-output`:

- `GoPro-VBOX-Sync-<version>-Setup.exe`: per-user installer, optional desktop shortcut and standard uninstall.
- `GoPro-VBOX-Sync-<version>-Portable.zip`: extract the whole directory; settings/tools still use the local app-data folder.
- `GoPro-VBOX-Sync-<version>-Source.zip`: explicit allowlist from `scripts/public_files.py`.
- `SHA256SUMS.txt`, `build-info.json`, and the installed-app test's `validation.json`.

Source and binaries use the same version in `goprovbox/__init__.py` and `pyproject.toml`. Also update the numeric Windows version resource, guide, README download link and release notes for a new version. `scripts/make_demo.py` regenerates the original synthetic assets using Pillow and FFmpeg. Never substitute real recordings in the bundled demo.

## Publishing

The GitHub Actions workflow tests each main-branch push and pull request. Pushing a version tag such as `v1.4.0b1` additionally builds and tests the installer, then creates a public **prerelease** with its assets. The tag must match the app version. No personal access token is stored: the release step uses the job's repository-scoped `GITHUB_TOKEN`. Release write permission is limited to the tag build job. Actions are pinned to reviewed commit hashes.

Use the repository README and releases page as the public download page. A standalone `docs/index.html` landing page is supplied if you later enable GitHub Pages from the `main` branch's `/docs` directory. Publishing Pages is optional; download links already work through GitHub Releases.

Review the allowlist, diff and release assets before pushing. `Examples`, `scratch`, local validation records, local build tools, environments and recordings are excluded. Releases contain no Racelogic libraries, scene artwork or FFmpeg binaries. FFmpeg is downloaded separately by the user.

Keep third-party licence notices with binaries. Build-script copies of Python, Pillow and PyInstaller notices come from the selected build environment; the retained Tcl/Tk, OpenSSL and mimalloc notices must be checked when updating the runtime. No signing certificate is configured. Do not describe an unsigned build as signed or guarantee that Windows will suppress reputation warnings.
