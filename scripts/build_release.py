"""Build the installer, portable app and reviewed-source archive on Windows."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile

from public_files import ROOT, public_files
sys.path.insert(0, str(ROOT))
from goprovbox import __version__


def build_icons():
    """Package the supplied brand artwork at the sizes used by Windows."""
    from PIL import Image
    icons = ROOT / "goprovbox/assets"
    with Image.open(icons / "brand.png") as artwork:
        im = artwork.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
    im.save(icons / "icon.png")
    im.save(icons / "icon.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


def main():
    if os.name != "nt": raise SystemExit("Build on 64-bit Windows")
    os.chdir(ROOT)
    icons = ROOT / "goprovbox/assets"
    build_icons()
    notices = icons / "licenses"; notices.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", notices / "MIT.txt")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", notices / "Python.txt")
    for distname, target in [("Pillow", "Pillow.txt"), ("PyInstaller", "PyInstaller.txt")]:
        dist = importlib.metadata.distribution(distname)
        matches = [p for p in dist.files if p.name in ("LICENSE", "COPYING.txt", "COPYING") and "dist-info" in str(p)]
        if not matches: raise RuntimeError(f"Missing {distname} licence")
        shutil.copy2(dist.locate_file(matches[0]), notices / target)
    for name in ("Tcl", "Tk", "OpenSSL", "mimalloc"):
        if not (notices / (name + ".txt")).is_file(): raise RuntimeError(f"Missing {name} notice")
    build = ROOT / "build"; build.mkdir(exist_ok=True)
    (build / "version-info.txt").write_text('''VSVersionInfo(
      ffi=FixedFileInfo(filevers=(1,5,0,1), prodvers=(1,5,0,1), mask=0x3f, flags=2,
                       OS=0x40004, fileType=1, subtype=0, date=(0,0)),
      kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName','GoPro VBOX Sync contributors'),
        StringStruct('FileDescription','GoPro VBOX Sync (beta)'),
        StringStruct('FileVersion','VERSION'),
        StringStruct('ProductName','GoPro VBOX Sync'),
        StringStruct('ProductVersion','VERSION'),
        StringStruct('OriginalFilename','GoProVBOXSync.exe'),
        StringStruct('LegalCopyright','Copyright 2026 William Mulholland and contributors')
      ])]), VarFileInfo([VarStruct('Translation',[1033,1200])])])'''.replace("VERSION", __version__), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "GoProVBOXSync.spec"], check=True)
    compiler = Path(os.environ.get("ISCC", str(ROOT / ".build-tools/InnoSetup/ISCC.exe")))
    if not compiler.is_file(): raise SystemExit("Run scripts/bootstrap_inno.ps1 first, or set ISCC")
    output = ROOT / "release-output"; output.mkdir(exist_ok=True)
    subprocess.run([str(compiler), "/Qp", f"/DAppVersion={__version__}", "release/installer.iss"], check=True)
    stem = f"GoPro-VBOX-Sync-{__version__}"
    with zipfile.ZipFile(output / (stem + "-Portable.zip"), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted((ROOT / "dist/GoProVBOXSync").rglob("*")):
            if file.is_file(): archive.write(file, file.relative_to(ROOT / "dist"))
    with zipfile.ZipFile(output / (stem + "-Source.zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for file in public_files(): archive.write(file, "gopro-vbox-sync/" + file.relative_to(ROOT).as_posix())
    provenance = {"app": __version__, "python": sys.version.split()[0],
                  "dependencies": {name: importlib.metadata.version(name) for name in ("Pillow", "PyInstaller", "pyinstaller-hooks-contrib")},
                  "ffmpeg": "8.1.2, downloaded separately; not bundled", "inno_setup": "6.7.3", "signed": False}
    (output / "build-info.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    artifacts = [output / (stem + suffix) for suffix in ("-Setup.exe", "-Portable.zip", "-Source.zip")] + [output / "build-info.json"]
    (output / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in artifacts), encoding="ascii")
    print(f"Release files: {output}")


if __name__ == "__main__": main()
