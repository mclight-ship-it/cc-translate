"""Bundle entry point, executed with python -I -B (not as a module)."""

from pathlib import Path
import sys


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cc_macos.server import main

    raise SystemExit(main())
