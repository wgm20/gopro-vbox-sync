"""Explicit publication scope; private recordings and experiments never qualify."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (".gitignore", ".gitattributes", "LICENSE", "README.md", "CONTRIBUTING.md", "BUILDING.md",
              "CHANGELOG.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "requirements-build.txt",
              "GoProVBOXSync.spec", "launch.pyw")
PATTERNS = ("goprovbox/*.py", "goprovbox/assets/*.html", "goprovbox/assets/*.png",
            "goprovbox/assets/*.ico", "goprovbox/assets/demo/*.MP4", "goprovbox/assets/demo/*.vbo",
            "goprovbox/assets/demo/*.txt", "goprovbox/assets/licenses/*.txt", "tests/*.py",
            "scripts/*.py", "scripts/*.ps1", "release/*.iss", "release/*.txt", "release/*.md",
            ".github/workflows/*.yml", ".github/ISSUE_TEMPLATE/*.md", "docs/*.html")


def public_files():
    files = {ROOT / name for name in ROOT_FILES}
    for pattern in PATTERNS:
        files.update(ROOT.glob(pattern))
    for file in files:
        if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(ROOT):
            raise ValueError(f"Invalid publication file: {file}")
    return sorted(files)


if __name__ == "__main__":
    for file in public_files():
        print(file.relative_to(ROOT).as_posix())
