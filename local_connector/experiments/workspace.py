"""Experiment copies: a throw-away copy of the project with the experiment's
changes applied. The researcher's own folder is only ever read."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from backend.orchestration.evidence_policy import path_problem
from local_connector.experiments.manifest import EntryPoint
from local_connector.file_access.walk import SKIP_DIRS

MAX_COPY_BYTES = 500 * 1024 * 1024
SCALARS = (int, float, str, bool)


class ExperimentRefused(ValueError):
    """The experiment can't be prepared as asked."""


def copy_project(root: Path, dest: Path) -> int:
    """Copy the project into dest, leaving out tool folders and sensitive files
    (.git, .env, keys, ...). Returns the bytes copied."""
    root, dest, total = Path(root).resolve(), Path(dest).resolve(), 0
    if dest == root or root in dest.parents:
        raise ExperimentRefused("Save project versions outside the source project folder.")
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in sorted(dirnames)
                       if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
                       and not d.endswith(".egg-info") and not path_problem((rel_dir / d / "x").as_posix())]
        for name in sorted(filenames):
            relative = rel_dir / name
            if path_problem(relative.as_posix()):
                continue
            source = Path(dirpath) / name
            if source.is_symlink():
                continue
            total += source.stat().st_size
            if total > MAX_COPY_BYTES:
                shutil.rmtree(dest, ignore_errors=True)
                raise ExperimentRefused(
                    f"The project is over {MAX_COPY_BYTES // (1024 * 1024)} MB, too large to copy for an experiment.")
            (dest / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / relative)
    return total


def read_parameters(root: Path, entry: EntryPoint) -> dict:
    """The current values of the entry point's parameters in the project's config."""
    if not entry.config:
        return {}
    config = _load_config(Path(root) / entry.config, entry.config)
    return {key: config.get(key) for key in entry.parameters}


def check_changes(root: Path, entry: EntryPoint, changes: dict) -> dict:
    """Refuse changes the manifest doesn't allow. Returns {key: [old, new]}."""
    if not changes:
        return {}
    if not entry.config:
        raise ExperimentRefused(f'"{entry.name}" has no config to change.')
    config = _load_config(Path(root) / entry.config, entry.config)
    result = {}
    for key, value in changes.items():
        if key not in entry.parameters:
            raise ExperimentRefused(f'"{key}" is not a parameter experiments may change (see expandrg.json).')
        if key not in config:
            raise ExperimentRefused(f'"{key}" is not in {entry.config}.')
        if not isinstance(value, SCALARS) or (isinstance(config[key], (int, float)) and not isinstance(value, (int, float))):
            raise ExperimentRefused(f'"{key}" must be a {type(config[key]).__name__}, like its value in {entry.config}.')
        result[key] = [config[key], value]
    return result


def apply_changes(copy_root: Path, entry: EntryPoint, changes: dict) -> None:
    """Write the checked changes into the copy's config (never the project's)."""
    if not changes:
        return
    path = Path(copy_root) / entry.config
    config = _load_config(path, entry.config)
    config.update({key: new for key, (_, new) in changes.items()})
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _load_config(path: Path, name: str) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ExperimentRefused(f"{name} is not in the project.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ExperimentRefused(f"{name} is not valid JSON.") from None
    if not isinstance(config, dict):
        raise ExperimentRefused(f"{name} must hold a JSON object.")
    return config
