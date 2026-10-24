"""Start everything for an ExpandRG demo on this computer and open the dashboard.

    python run_demo.py           start the backend, local connector and dashboard,
                                 then open Researcher A and Researcher B in two windows
    python run_demo.py --fresh   first reset the demo data to a clean starting point
    python run_demo.py --check   start everything, check it works, then stop (no browser)

Ctrl+C stops everything. Needs backend/.env filled in and `npm install` run in frontend/.
Service output goes to .demo-logs/ in case something doesn't start.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REQUIRED_PACKAGES = {"fastapi": "fastapi", "uvicorn": "uvicorn", "requests": "requests", "dotenv": "python-dotenv"}
missing = [pip_name for module, pip_name in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module) is None]
if missing:
    sys.exit(
        f"This Python is missing {', '.join(missing)}:\n  {sys.executable}\n"
        "Install the project's packages for it (from the repo folder):\n"
        "  python -m pip install -r backend/requirements.txt"
    )

import requests  # noqa: E402  (checked above)

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / ".demo-logs"
DASHBOARD = "http://127.0.0.1:5173"
SAMPLE_FOLDERS = [ROOT / "tests" / "fixtures" / "sample_project", ROOT / "tests" / "fixtures" / "sample_project_b"]


def services() -> list[tuple[str, list[str], Path, str]]:
    npm = shutil.which("npm") or "npm"
    return [
        ("backend", [sys.executable, "-m", "uvicorn", "backend.api.app:app", "--port", "8000"], ROOT,
         "http://127.0.0.1:8000/health"),
        ("local connector", [sys.executable, "-m", "local_connector", "serve"], ROOT, "http://127.0.0.1:8001/health"),
        ("dashboard", [npm, "run", "dev"], ROOT / "frontend", f"{DASHBOARD}/"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ExpandRG demo on this computer.")
    parser.add_argument("--fresh", action="store_true", help="reset the demo data first (deletes local demo databases)")
    parser.add_argument("--check", action="store_true", help="start everything, check it, then stop")
    args = parser.parse_args()

    if not (ROOT / "backend" / ".env").exists():
        print("backend/.env is missing. Copy backend/.env.example to backend/.env and fill in the CREAO keys.")
        return 1
    if not (ROOT / "frontend" / "node_modules").exists():
        print("The dashboard isn't installed yet. Run:  cd frontend  then  npm install")
        return 1

    demo = [sys.executable, "-m", "backend.workspaces.demo"] + (["--reset"] if args.fresh else [])
    print("Resetting the demo data..." if args.fresh else "Making sure the demo data is in place...")
    subprocess.run(demo, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)

    LOGS.mkdir(exist_ok=True)
    running: list[subprocess.Popen] = []
    try:
        for name, command, cwd, health in services():
            log = open(LOGS / f"{name.replace(' ', '_')}.log", "w", encoding="utf-8")
            running.append(subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT))
            if not _wait_for(health):
                print(f"The {name} didn't start. See .demo-logs/{name.replace(' ', '_')}.log")
                return 1
            print(f"  {name} is running")

        for folder in SAMPLE_FOLDERS:
            requests.post(f"{DASHBOARD}/connector/grants", json={"root": str(folder)}, timeout=10).raise_for_status()
        print("  sample project folders granted to the local connector")

        if args.check:
            print("Everything works.")
            return 0

        windows = {
            "Researcher A": f"{DASHBOARD}/?as=researcher_a&view=diagnose&project=heat-diffusion",
            "Researcher B": f"{DASHBOARD}/?as=researcher_b&view=superbrain&project=heat-diffusion",
        }
        for url in windows.values():
            webbrowser.open_new(url)
            time.sleep(1)
        print("\nOpened:")
        for person, url in windows.items():
            print(f"  {person}: {url}")
        print(f"  Group leader (open when you need it): {DASHBOARD}/?as=leader&view=offboarding")
        print("\nPut the two windows side by side (Windows key + Left / Right arrow).")
        print("Demo script: Documents/demo-script.md")
        print("Press Ctrl+C here to stop everything.")
        while all(p.poll() is None for p in running):
            time.sleep(1)
        print("A service stopped unexpectedly. See .demo-logs/.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    finally:
        for process in reversed(running):
            _stop(process)


def _wait_for(url: str, seconds: float = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=1).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":  # also stops child processes, e.g. node under npm
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        process.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
