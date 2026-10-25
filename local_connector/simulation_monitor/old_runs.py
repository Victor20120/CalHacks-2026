"""Read runs that already finished, so they show on the dashboard like a watched run.

For runs made before ExpandRG, or outside it: the numbers in the run's console
log, including lines with no step number and no "=" ("z-drift/beat -2.077E-02
steadiness 0.002", one line per beat), and the one-number-per-line files a
Fortran code writes (fort.80, ...), named from the comment or variable in the
run's own source. Read-only: nothing in the run's folder is changed.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.monitoring.runs import MAX_METRICS_PER_RUN
from local_connector.simulation_monitor.checks import _NUMBER, Alert, LogMonitor, Sample, sample_of

LOG_SUFFIXES = (".log", ".out")
NOT_RUN_LOGS = {"build.log", "make.log", "compile.log"}
UNIT_FILE = re.compile(r"fort\.(\d+)")
SOURCE_SUFFIXES = (".f90", ".f", ".f95", ".f03", ".for")
SKIPPED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}
MAX_LOG_BYTES = 2_000_000
MAX_UNIT_FILE_BYTES = 20_000_000
# A big one-column file nothing names is usually a value per node per output (velocities), not one series.
MAX_UNNAMED_BYTES = 100_000
MAX_POSITION_BYTES = 200_000_000  # fort.10, the rod's positions every output
MAX_ROWS = 1_000  # per series; longer ones are thinned evenly, keeping their row numbers
MAX_RUNS = 500
MAX_LABEL = 100  # the backend's limit on a run's name

_LABEL = r"[A-Za-z][\w/|.\-]*(?:\s[A-Za-z][\w/|.\-]*)*"
_LOOSE_PAIR = re.compile(rf"({_LABEL})\s*[=:]?\s+({_NUMBER})(?![\w.])", re.IGNORECASE)
_ONLY_PAIRS = re.compile(rf"\s*(?:{_LABEL}\s*[=:]?\s+{_NUMBER}(?![\w.])\s*)+", re.IGNORECASE)


@dataclass
class OldRun:
    label: str  # the run folder, relative to the granted folder
    samples: list[Sample] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)


def find_runs(folder: Path) -> list[Path]:
    """Folders under `folder` (itself included) holding a run log or fort.N output."""
    found = []
    for here, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith("."))
        if any(_is_log(f) or UNIT_FILE.fullmatch(f) for f in files):
            found.append(Path(here))
            if len(found) >= MAX_RUNS:
                break
    return found


def read_run(run_dir: Path, label: str) -> OldRun:
    run = OldRun(label[-MAX_LABEL:])
    swimmer = swimmer_series(run_dir)
    if swimmer:  # the group's swimmer runs: the graphs they always use, nothing else
        return _finish(run, swimmer, list(swimmer))
    series: dict[str, dict[int, float | None]] = {}
    monitor = LogMonitor()
    for log in sorted(p for p in run_dir.iterdir() if p.is_file() and _is_log(p.name)):
        if log.stat().st_size > MAX_LOG_BYTES:
            continue
        loose = 0
        for number, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            run.alerts += monitor.feed(line, number)
            stepped = sample_of(line)
            if stepped:
                _add(series, stepped.step, stepped.values)
            elif _ONLY_PAIRS.fullmatch(line):
                loose += 1  # a line per beat or per output: number them in order
                _add(series, loose, {name.strip(): _number(value) for name, value in _LOOSE_PAIR.findall(line)})
    names = _unit_names(run_dir)
    for path in sorted((p for p in run_dir.iterdir() if p.is_file() and UNIT_FILE.fullmatch(p.name)),
                       key=lambda p: int(p.name.split(".")[1])):
        unit = int(path.name.split(".")[1])
        if unit not in names and path.stat().st_size > MAX_UNNAMED_BYTES:
            continue
        values = _one_column(path)
        if values:
            name = f"{names[unit]} (fort.{unit})" if unit in names else path.name
            series[name] = dict(_thin(values))
    # The log's repeated numbers first, then the output files; a number seen once isn't a graph.
    return _finish(run, series, [name for name, points in series.items() if len(points) >= 2][:MAX_METRICS_PER_RUN])


def swimmer_series(run_dir: Path) -> dict[str, dict[int, float | None]] | None:
    """Distance, velocity and efficiency per beat, worked out as the group's compare_v3 does (its
    original "chord" velocity): from the rod's positions (fort.10, np points a frame) and the energy
    each beat (fort.80). None unless the folder has both and says np."""
    settings = _settings(run_dir)
    points, positions = settings.get("np"), run_dir / "fort.10"
    energy = _one_column(run_dir / "fort.80") if (run_dir / "fort.80").is_file() else []
    if not points or not positions.is_file() or not energy or positions.stat().st_size > MAX_POSITION_BYTES:
        return None
    centres = _centres(positions, int(points))
    period = 1 / settings.get("omFac", 1e3)  # the beat period, in the code's time units
    if {"dt", "nw"} <= settings.keys():
        per_beat = round(1 / (settings["dt"] * settings.get("omFac", 1e3)) / settings["nw"])  # nbeat / nw
    else:
        per_beat = (len(centres) - 1) // len(energy)
    beats = (len(centres) - 1) // per_beat if per_beat else 0
    if beats < 2:
        return None
    each = max(1, round(len(energy) / beats))  # energies written per beat
    energy = [None if None in energy[i:i + each] else sum(energy[i:i + each]) for i in range(0, each * beats, each)]
    distance, velocity, efficiency = {}, {}, {}
    for beat in range(1, min(beats, len(energy)) + 1):
        start, end = centres[(beat - 1) * per_beat], centres[beat * per_beat]
        distance[beat] = math.dist(end, centres[0])
        if beat == 1:
            continue  # it starts from rest; compare_v3 leaves the first beat out
        velocity[beat] = math.dist(end, start)
        power = energy[beat - 1] / period if energy[beat - 1] is not None else None
        efficiency[beat] = velocity[beat] ** 2 / power if power else None
    return {"distance from start (body lengths)": distance, "velocity (body lengths / beat)": velocity,
            "efficiency (velocity^2 / power)": efficiency}


def _finish(run: OldRun, series: dict, kept: list[str]) -> OldRun:
    run.metrics = kept
    steps = sorted({step for name in kept for step in series[name]})
    run.samples = [Sample(step, {name: series[name][step] for name in kept if step in series[name]}) for step in steps]
    for name in kept:
        bad = next((step for step, value in sorted(series[name].items()) if value is None), None)
        if bad is not None:
            run.alerts.append(Alert("critical", "nan_inf", f"{name} became NaN or Inf at {bad}.", name[:100], None, "", bad))
    return run


def _settings(run_dir: Path) -> dict[str, float]:
    """np, nw, dt and omFac as the run's setup.txt and parameters.f90 give them (comments ignored)."""
    found: dict[str, float] = {}
    for name in ("setup.txt", "parameters.f90", "params.inc"):
        path = run_dir / name
        if not path.is_file() or path.stat().st_size > 500_000:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            code = line.split("!")[0]
            for key in ("np", "nw", "dt", "omFac"):
                hit = re.search(rf"\b{key}\b\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][-+]?\d+)?)", code)
                if hit and key not in found:
                    found[key] = float(hit.group(1).replace("d", "e").replace("D", "e"))
    return {k: v for k, v in found.items() if v > 0}


def _centres(path: Path, points: int) -> list[tuple[float, float, float]]:
    """The rod's mean position in each frame of a file with x y z per line, `points` lines a frame."""
    centres, total, count = [], [0.0, 0.0, 0.0], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            words = line.split()
            if len(words) < 3:
                continue
            for axis in range(3):
                total[axis] += float(words[axis].replace("D", "E").replace("d", "e"))
            count += 1
            if count == points:
                centres.append((total[0] / points, total[1] / points, total[2] / points))
                total, count = [0.0, 0.0, 0.0], 0
    return centres


def _is_log(name: str) -> bool:
    return name.lower().endswith(LOG_SUFFIXES) and name.lower() not in NOT_RUN_LOGS


def _add(series: dict, step: int, values: dict) -> None:
    for name, value in values.items():
        series.setdefault(name[:100], {})[step] = value


def _number(text: str) -> float | None:
    value = float(text)
    return value if math.isfinite(value) else None


def _one_column(path: Path) -> list[float | None]:
    """The numbers in a file with one per line; empty if it holds anything else."""
    if path.stat().st_size > MAX_UNIT_FILE_BYTES:
        return []
    values = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                values.append(_number(text.replace("D", "E").replace("d", "e")))
            except ValueError:
                return []  # several columns (positions, forces per node): not a series over time
    return values


def _thin(values: list[float | None]) -> list[tuple[int, float | None]]:
    rows = list(enumerate(values, start=1))
    if len(rows) <= MAX_ROWS:
        return rows
    stride = len(rows) / MAX_ROWS
    return [rows[int(i * stride)] for i in range(MAX_ROWS - 1)] + [rows[-1]]


def _unit_names(run_dir: Path) -> dict[int, str]:
    """What the run's own code says each fort.N holds: a "! fort.90 : body-only power" comment,
    else the variable written there ("write(90, ...) pBody")."""
    names: dict[int, str] = {}
    for source in run_dir.iterdir():
        if not source.is_file() or not source.name.lower().endswith(SOURCE_SUFFIXES) or source.stat().st_size > 500_000:
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        for unit, said in re.findall(r"!\s*fort\.(\d+)\s*(?:\.\.)?\s*:\s*([^!\n]+)", text):
            names.setdefault(int(unit), said.strip()[:40])
        for unit, variable in re.findall(r"""write\s*\(\s*(\d+)\s*,\s*(?:'[^']*'|"[^"]*"|[^)'"]*)\s*\)\s*([A-Za-z]\w*)""",
                                         text, re.IGNORECASE):
            names.setdefault(int(unit), variable)
    return names
