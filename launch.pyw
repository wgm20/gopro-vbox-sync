"""Desktop shortcut target, also accepts a dropped recordings folder."""
from pathlib import Path
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        import traceback
        from goprovbox.selftest import run_self_test
        try:
            run_self_test(Path(sys.argv[2]))
        except Exception:
            Path(sys.argv[2] + ".error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
    else:
        from goprovbox.gui import launch
        launch(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
