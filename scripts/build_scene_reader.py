"""Build the app-owned .NET host; vendor assemblies stay in their installation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goprovbox.scene_reader import build_helper


if __name__ == "__main__":
    print(build_helper(ROOT / "goprovbox" / "assets"))
