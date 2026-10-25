"""Start and stop ExpandRG on this computer: the backend, the local connector and the dashboard.

`expandrg start` (or `python run_demo.py` in a checkout) runs the demo group, or with --group a
research group made with `expandrg setup`. The dashboard is the Vite dev server when a checkout
has frontend/node_modules; otherwise the built pages inside the package (no Node needed).
Service output goes to the logs folder (paths.logs_dir) in case something doesn't start.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import requests

from expandrg.paths import FROM_SOURCE, SOURCE, command, demo_env_file, home, logs_dir

DASHBOARD = "http://127.0.0.1:5173"
HEALTH = ("http://127.0.0.1:8000/health", "http://127.0.0.1:8001/health")
PAGES = Path(__file__).resolve().parent / "pages"
SAMPLE_FOLDERS = [SOURCE / "tests" / "fixtures" / "sample_project", SOURCE / "tests" / "fixtures" / "sample_project_b"]
_LAUNCHER = re.compile(r"run_demo\.py|\bexpandrg(\.exe)?\"?\s+start\b")
_STOPPER = re.compile(r"--stop\b|\bexpandrg(\.exe)?\"?\s+stop\b")


def workdir() -> Path:
    """Where the services run: the checkout (so its packages are found), or ExpandRG's own folder,
    so a project folder's own modules (a `backend` package, say) can't stand in for ExpandRG's."""
    if FROM_SOURCE:
        return SOURCE
    home().mkdir(parents=True, exist_ok=True)
    return home()


def dev_dashboard() -> bool:
    return FROM_SOURCE and (SOURCE / "frontend" / "node_modules").exists()


def services() -> list[tuple[str, list[str], Path, str]]:
    if dev_dashboard():
        dashboard = [shutil.which("npm") or "npm", "run", "dev"], SOURCE / "frontend"
    else:
        dashboard = [sys.executable, "-m", "uvicorn", "expandrg.dashboard:app", "--port", "5173"], workdir()
    return [
        ("backend", [sys.executable, "-m", "uvicorn", "backend.api.app:app", "--port", "8000"], workdir(), HEALTH[0]),
        ("local connector", [sys.executable, "-m", "local_connector", "serve"], workdir(), HEALTH[1]),
        ("dashboard", dashboard[0], dashboard[1], f"{DASHBOARD}/"),
    ]


def start(group: str | None = None, fresh: bool = False, check: bool = False, no_browser: bool = False) -> int:
    serving = running_group()
    if serving:
        # Its backend would answer for the one being started, which couldn't take the port.
        print(f"ExpandRG is already running ({'the demo group' if serving == 'demo' else 'group ' + serving}).")
        print(f"Stop it first: {command('stop')}")
        return 1
    group_env = None
    if group:
        from backend.workspaces.groups import GroupError, describe, group_environment
        try:
            group_env = {**os.environ, **group_environment(group)}
        except GroupError as exc:
            print(exc)
            return 1
    elif not demo_env_file().exists():
        if FROM_SOURCE:
            print("backend/.env is missing. Copy backend/.env.example to backend/.env and fill in the CREAO keys,")
        else:
            print(f"The demo group needs its CREAO keys in {demo_env_file()}.")
        print(f"or set up your own research group: {command('setup')}, then {command('start')} --group <id>")
        return 1
    if not dev_dashboard() and not (PAGES / "index.html").exists():
        print("The dashboard isn't installed yet. Run:  cd frontend  then  npm install" if FROM_SOURCE
              else "This ExpandRG package has no dashboard pages. Reinstall it from a wheel made with build_package.py.")
        return 1

    if not group:
        demo = [sys.executable, "-m", "backend.workspaces.demo"] + (["--reset"] if fresh else [])
        print("Resetting the demo data..." if fresh else "Making sure the demo data is in place...")
        subprocess.run(demo, cwd=workdir(), check=True, stdout=subprocess.DEVNULL)

    logs = logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    running: list[subprocess.Popen] = []
    try:
        for name, cmd, cwd, health in services():
            log_file = logs / f"{name.replace(' ', '_')}.log"
            log = open(log_file, "w", encoding="utf-8")
            running.append(subprocess.Popen(cmd, cwd=cwd, env=group_env, stdout=log, stderr=subprocess.STDOUT))
            if not _wait_for(health):
                print(f"The {name} didn't start. See {log_file}")
                return 1
            print(f"  {name} is running")

        samples = [f for f in SAMPLE_FOLDERS if f.is_dir()]
        if not group and samples:
            for folder in samples:
                requests.post(f"{DASHBOARD}/connector/grants", json={"root": str(folder)}, timeout=10).raise_for_status()
            print("  sample project folders granted to the local connector")

        if check:
            print("Everything works.")
            return 0

        if group:
            described = describe(group)
            project = described["projects"][0]["id"]
            print(f"\n{described['name']}: sign in as")
            for person in [described["leader"]] + described["researchers"]:
                view = "superbrain" if person is described["leader"] else "project"
                print(f"  {person['name']}: {DASHBOARD}/?as={person['id']}&view={view}&project={project}")
            if not no_browser:
                webbrowser.open_new(f"{DASHBOARD}/?as={described['leader']['id']}&view=superbrain&project={project}")
        else:
            windows = {
                "Researcher A": f"{DASHBOARD}/?as=researcher_a&view=diagnose&project=heat-diffusion",
                "Researcher B": f"{DASHBOARD}/?as=researcher_b&view=superbrain&project=heat-diffusion",
            }
            for url in [] if no_browser else windows.values():
                webbrowser.open_new(url)
                time.sleep(1)
            print("\nDashboard links:" if no_browser else "\nOpened:")
            for person, url in windows.items():
                print(f"  {person}: {url}")
            print(f"  Group leader (open when you need it): {DASHBOARD}/?as=leader&view=offboarding")
            print("\nPut the two windows side by side (Windows key + Left / Right arrow).")
            if FROM_SOURCE:
                print("Demo script: Documents/demo-script.md")
        print("Press Ctrl+C here to stop everything.")
        while all(p.poll() is None for p in running):
            time.sleep(1)
        print(f"A service stopped unexpectedly. See {logs}")
        return 1
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    finally:
        for process in reversed(running):
            _stop(process)


def expandrg_processes(listing: list[tuple[int, str]], own: set[int]) -> list[int]:
    """The processes that are ExpandRG, from (pid, command line) pairs: launchers, the backend,
    the connector and the dashboard (the checkout's dev server or the packaged one). Never
    ourselves, a stop command or another app's dev server."""
    frontend = str(SOURCE / "frontend").lower()
    picked = []
    for pid, cmd in listing:
        text = cmd.lower()
        if pid in own or _STOPPER.search(text):
            continue
        if (_LAUNCHER.search(text) or "backend.api.app" in text or "local_connector serve" in text
                or "expandrg.dashboard:app" in text or ("vite" in text and frontend in text)):
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
        pid, _, cmd = line.strip().replace("\t", " ", 1).partition(" ")
        if pid.isdigit():
            listing.append((int(pid), cmd))
    found = expandrg_processes(listing, {os.getpid(), os.getppid()})
    for pid in found:
        if os.name == "nt":  # with the processes each one started
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    stopped = all(not _is_up(url) for url in HEALTH)
    for _ in range(20):
        if stopped:
            break
        time.sleep(0.5)
        stopped = all(not _is_up(url) for url in HEALTH)
    print("ExpandRG stopped." if found else "ExpandRG wasn't running.")
    return 0 if stopped else 1


def running_group() -> str | None:
    """The group a running ExpandRG backend serves ("demo" or a group ID), or None if none answers."""
    try:
        answer = requests.get(HEALTH[0], timeout=1)
        return answer.json().get("group", "demo") if answer.ok else None
    except (requests.RequestException, ValueError):
        return None


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
