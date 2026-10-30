"""Desktop shortcut target, also accepts a dropped recordings folder."""
from pathlib import Path
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        import argparse
        import traceback
        from goprovbox.selftest import run_self_test
        parser = argparse.ArgumentParser()
        parser.add_argument("destination", type=Path)
        parser.add_argument("--scene", type=Path)
        args = parser.parse_args(sys.argv[2:])
        try:
            run_self_test(args.destination, scene_path=args.scene)
        except Exception:
            Path(str(args.destination) + ".error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
    else:
        from goprovbox.__main__ import main
        sys.exit(main(["--gui", *sys.argv[1:]]))
