"""Start everything for an ExpandRG demo on this computer and open the dashboard.

    python run_demo.py           start the backend, local connector and dashboard,
                                 then open Researcher A and Researcher B in two windows
    python run_demo.py --fresh   first reset the demo data to a clean starting point
    python run_demo.py --check   start everything, check it works, then stop (no browser)
    python run_demo.py --no-browser   start everything without opening windows (the VS Code extension uses this)
    python run_demo.py --group ID     start a research group made with setup_expandrg.py instead of the demo
    python run_demo.py --stop         stop ExpandRG if it's running, wherever it was started

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
    parser.add_argument("--no-browser", action="store_true", help="don't open dashboard windows")
    parser.add_argument("--group", metavar="ID", help="start a research group made with setup_expandrg.py instead of the demo")
    parser.add_argument("--stop", action="store_true", help="stop ExpandRG if it's running, wherever it was started")
    args = parser.parse_args()
    if args.stop:
        return stop_running()

    group_env = None
    if args.group:
        from backend.workspaces.groups import GroupError, describe, group_environment
        try:
            group_env = {**os.environ, **group_environment(args.group)}
        except GroupError as exc:
            print(exc)
            return 1
    elif not (ROOT / "backend" / ".env").exists():
        print("backend/.env is missing. Copy backend/.env.example to backend/.env and fill in the CREAO keys,")
        print("or set up your own research group: python setup_expandrg.py")
        return 1
    if not (ROOT / "frontend" / "node_modules").exists():
        print("The dashboard isn't installed yet. Run:  cd frontend  then  npm install")
        return 1

    if not args.group:
        demo = [sys.executable, "-m", "backend.workspaces.demo"] + (["--reset"] if args.fresh else [])
        print("Resetting the demo data..." if args.fresh else "Making sure the demo data is in place...")
        subprocess.run(demo, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)

    LOGS.mkdir(exist_ok=True)
    running: list[subprocess.Popen] = []
    try:
        for name, command, cwd, health in services():
            log = open(LOGS / f"{name.replace(' ', '_')}.log", "w", encoding="utf-8")
            running.append(subprocess.Popen(command, cwd=cwd, env=group_env, stdout=log, stderr=subprocess.STDOUT))
            if not _wait_for(health):
                print(f"The {name} didn't start. See .demo-logs/{name.replace(' ', '_')}.log")
                return 1
            print(f"  {name} is running")

        if not args.group:
            for folder in SAMPLE_FOLDERS:
                requests.post(f"{DASHBOARD}/connector/grants", json={"root": str(folder)}, timeout=10).raise_for_status()
            print("  sample project folders granted to the local connector")

        if args.check:
            print("Everything works.")
            return 0

        if args.group:
            group = describe(args.group)
            project = group["projects"][0]["id"]
            print(f"\n{group['name']}: sign in as")
            people = [group["leader"]] + group["researchers"]
            for person in people:
                view = "superbrain" if person is group["leader"] else "project"
                print(f"  {person['name']}: {DASHBOARD}/?as={person['id']}&view={view}&project={project}")
            if not args.no_browser:
                webbrowser.open_new(f"{DASHBOARD}/?as={group['leader']['id']}&view=superbrain&project={project}")
            print("Press Ctrl+C here to stop everything.")
            while all(p.poll() is None for p in running):
                time.sleep(1)
            print("A service stopped unexpectedly. See .demo-logs/.")
            return 1

        windows = {
            "Researcher A": f"{DASHBOARD}/?as=researcher_a&view=diagnose&project=heat-diffusion",
            "Researcher B": f"{DASHBOARD}/?as=researcher_b&view=superbrain&project=heat-diffusion",
        }
        for url in [] if args.no_browser else windows.values():
            webbrowser.open_new(url)
            time.sleep(1)
        print("\nDashboard links:" if args.no_browser else "\nOpened:")
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


def expandrg_processes(listing: list[tuple[int, str]], own: set[int]) -> list[int]:
    """The processes that are ExpandRG, from (pid, command line) pairs: launchers, the backend,
    the connector and this folder's dashboard. Never ourselves or another app's dev server."""
    frontend = str(ROOT / "frontend").lower()
    picked = []
    for pid, command in listing:
        text = command.lower()
        if pid in own or "--stop" in text:
            continue
        if ("run_demo.py" in text or "backend.api.app" in text or "local_connector serve" in text
                or ("vite" in text and frontend in text)):
            picked.append(pid)
    return picked


def stop_running() -> int:
    """Stop a running ExpandRG (any group), e.g. before VS Code starts another group."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
                  "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout
    else:
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    listing = []
    for line in out.splitlines():
        pid, _, command = line.strip().replace("\t", " ", 1).partition(" ")
        if pid.isdigit():
            listing.append((int(pid), command))
    found = expandrg_processes(listing, {os.getpid(), os.getppid()})
    for pid in found:
        if os.name == "nt":  # with the processes each one started
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    stopped = all(not _is_up(url) for url in ("http://127.0.0.1:8000/health", "http://127.0.0.1:8001/health"))
    for _ in range(20):
        if stopped:
            break
        time.sleep(0.5)
        stopped = all(not _is_up(url) for url in ("http://127.0.0.1:8000/health", "http://127.0.0.1:8001/health"))
    print("ExpandRG stopped." if found else "ExpandRG wasn't running.")
    return 0 if stopped else 1


def _is_up(url: str) -> bool:
    try:
        return requests.get(url, timeout=1).ok
    except requests.RequestException:
        return False


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
