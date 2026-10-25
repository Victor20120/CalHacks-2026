"""Where ExpandRG keeps its files, and how to run its commands here.

Run from a source checkout (the folder with run_demo.py), the demo keeps using backend/data,
backend/.env and .demo-logs as before. Installed as a package, everything lives in
$EXPANDRG_HOME (default ~/.expandrg), never inside the installed files.
"""
from __future__ import annotations

import os
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
FROM_SOURCE = (SOURCE / "run_demo.py").exists() and (SOURCE / "backend").is_dir()

_SOURCE_COMMANDS = {
    "start": "python run_demo.py",
    "stop": "python run_demo.py --stop",
    "setup": "python setup_expandrg.py",
    "add-project": "python setup_expandrg.py --add-project",
}


def home() -> Path:
    return Path(os.environ.get("EXPANDRG_HOME") or Path.home() / ".expandrg")


def demo_data_dir() -> Path:
    return SOURCE / "backend" / "data" if FROM_SOURCE else home() / "demo" / "data"


def demo_env_file() -> Path:
    """The demo's CREAO keys. A research group's own keys are in its group.env."""
    return SOURCE / "backend" / ".env" if FROM_SOURCE else home() / "demo.env"


def logs_dir() -> Path:
    return SOURCE / ".demo-logs" if FROM_SOURCE else home() / "logs"


def command(name: str) -> str:
    """How to run an ExpandRG command here: the scripts in a checkout, the expandrg command installed."""
    return _SOURCE_COMMANDS[name] if FROM_SOURCE else f"expandrg {name}"
