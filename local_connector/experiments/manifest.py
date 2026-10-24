"""The run manifest: what experiments may run in a project, and what they may change.

The researcher writes it once, as expandrg.json in the project folder:

    {
      "entry_points": [{
        "name": "heat2d",
        "command": ["{python}", "heat2d.py", "config.json"],
        "config": "config.json",
        "parameters": ["dt", "dx", "steps"],
        "limits": {"wall_seconds": 300, "memory_mb": 1024, "output_mb": 20}
      }]
    }

"{python}" is the Python running ExpandRG. Experiments only run these commands,
in a copy of the project, and may only change the listed top-level keys of the
entry point's JSON config. The researcher's own project is never changed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from backend.orchestration.evidence_policy import path_problem

MANIFEST_NAME = "expandrg.json"
DEFAULT_LIMITS = {"wall_seconds": 600, "memory_mb": 2048, "output_mb": 50}
MAX_LIMITS = {"wall_seconds": 6 * 3600, "memory_mb": 64 * 1024, "output_mb": 1024}
MAX_MANIFEST_BYTES = 100_000
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

EXAMPLE = """{
  "entry_points": [{
    "name": "main",
    "command": ["{python}", "solver.py", "config.json"],
    "config": "config.json",
    "parameters": ["dt", "steps"],
    "limits": {"wall_seconds": 300, "memory_mb": 1024, "output_mb": 20}
  }]
}"""


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    wall_seconds: int
    memory_mb: int
    output_mb: int


@dataclass(frozen=True)
class EntryPoint:
    name: str
    command: tuple[str, ...]
    config: str | None  # JSON file the parameters live in, relative to the project
    parameters: tuple[str, ...]
    limits: Limits

    def to_dict(self) -> dict:
        return {"name": self.name, "command": list(self.command), "config": self.config,
                "parameters": list(self.parameters), "limits": vars(self.limits)}


def load_manifest(root: Path) -> dict[str, EntryPoint]:
    """The project's entry points by name. Raises ManifestError with what to fix."""
    path = Path(root) / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"This folder has no {MANIFEST_NAME}. Add one to say what experiments may run, e.g.:\n{EXAMPLE}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestError(f"{MANIFEST_NAME} is too large.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_NAME} is not valid JSON: {exc}") from None
    entries = data.get("entry_points") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f'{MANIFEST_NAME} needs an "entry_points" list.')
    result: dict[str, EntryPoint] = {}
    for entry in entries:
        point = _entry_point(entry)
        if point.name in result:
            raise ManifestError(f'Two entry points are called "{point.name}".')
        result[point.name] = point
    return result


def _entry_point(entry: object) -> EntryPoint:
    if not isinstance(entry, dict):
        raise ManifestError("Each entry point must be an object.")
    name = entry.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise ManifestError("Each entry point needs a short name (letters, digits, - _ .).")
    command = entry.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
        raise ManifestError(f'"{name}": "command" must be a list of words, e.g. ["{{python}}", "solver.py"].')
    config = entry.get("config")
    parameters = entry.get("parameters", [])
    if config is not None:
        if not isinstance(config, str) or path_problem(config) or not config.lower().endswith(".json"):
            raise ManifestError(f'"{name}": "config" must be a JSON file inside the project, e.g. "config.json".')
    if not isinstance(parameters, list) or not all(isinstance(p, str) and p for p in parameters):
        raise ManifestError(f'"{name}": "parameters" must be a list of config keys.')
    if parameters and config is None:
        raise ManifestError(f'"{name}": parameters need a "config" file to change.')
    raw_limits = entry.get("limits", {})
    if not isinstance(raw_limits, dict):
        raise ManifestError(f'"{name}": "limits" must be an object.')
    limits = {}
    for key, default in DEFAULT_LIMITS.items():
        value = raw_limits.get(key, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ManifestError(f'"{name}": limit "{key}" must be a positive number.')
        limits[key] = int(min(value, MAX_LIMITS[key]))
    return EntryPoint(name, tuple(command), config, tuple(dict.fromkeys(parameters)), Limits(**limits))
