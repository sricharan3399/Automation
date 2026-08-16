#!/usr/bin/env python
"""One-command launcher.

    python launcher.py

Performs the startup sequence, then starts the backend and opens the dashboard
in the default browser. It never starts a data-source query.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from backend.cli import main as cli_main
    except ImportError as exc:
        print("Dependencies are not installed.")
        print(f"  {exc}")
        print("\nRun the installer first:")
        print("  powershell -ExecutionPolicy Bypass -File install_windows.ps1")
        print("or install manually:")
        print("  python -m venv .venv")
        print("  .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        return 1

    argv = sys.argv[1:] or ["start"]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
