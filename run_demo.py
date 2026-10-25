"""Start everything for an ExpandRG demo on this computer and open the dashboard.

    python run_demo.py           start the backend, local connector and dashboard,
                                 then open Researcher A and Researcher B in two windows
    python run_demo.py --fresh   first reset the demo data to a clean starting point
    python run_demo.py --check   start everything, check it works, then stop (no browser)
    python run_demo.py --no-browser   start everything without opening windows (the VS Code extension uses this)
    python run_demo.py --group ID     start a research group made with setup_expandrg.py instead of the demo
    python run_demo.py --stop         stop ExpandRG if it's running, wherever it was started

Ctrl+C stops everything. Needs backend/.env filled in and `npm install` run in frontend/.
Service output goes to .demo-logs/ in case something doesn't start. Installed as a package,
the same is `expandrg start` / `expandrg stop` (see expandrg/launcher.py).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REQUIRED_PACKAGES = {"fastapi": "fastapi", "uvicorn": "uvicorn", "requests": "requests", "dotenv": "python-dotenv"}
missing = [pip_name for module, pip_name in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module) is None]
if missing:
    sys.exit(
        f"This Python is missing {', '.join(missing)}:\n  {sys.executable}\n"
        "Install the project's packages for it (from the repo folder):\n"
        "  python -m pip install -r backend/requirements.txt"
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))

from expandrg.launcher import start, stop_running  # noqa: E402  (checked above)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ExpandRG demo on this computer.")
    parser.add_argument("--fresh", action="store_true", help="reset the demo data first (deletes local demo databases)")
    parser.add_argument("--check", action="store_true", help="start everything, check it, then stop")
    parser.add_argument("--no-browser", action="store_true", help="don't open dashboard windows")
    parser.add_argument("--group", metavar="ID", help="start a research group made with setup_expandrg.py instead of the demo")
    parser.add_argument("--stop", action="store_true", help="stop ExpandRG if it's running, wherever it was started")
    args = parser.parse_args()
    if args.stop:
        return stop_running()
    return start(group=args.group, fresh=args.fresh, check=args.check, no_browser=args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
