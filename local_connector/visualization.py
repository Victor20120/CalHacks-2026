"""Read existing local logs for graphs without changing files or executing code."""
from __future__ import annotations

import json
import math
from pathlib import Path

from local_connector.file_access.grants import AccessDenied, GrantRegistry
from local_connector.simulation_monitor.checks import sample_of

MAX_LOG_BYTES = 100_000_000
MAX_LOG_POINTS = 1_000_000


def local_data(registry: GrantRegistry, label: str, full: bool = False, *, path: Path | None = None,
               source_root: Path | None = None) -> dict:
    """Only exact, unambiguous granted log paths, or a connector-owned saved run.

    A full read fails at its limits instead of silently returning a tail.
    """
    if path is None:
        matches = []
        for grant in registry.grants():
            try:
                candidate, _ = registry.check_path(label, grant)
                if candidate.is_file():
                    matches.append((candidate, grant.root))
            except AccessDenied:
                continue
        if len(matches) != 1:
            raise ValueError("The original log is unavailable or ambiguous in the granted folders. Recorded data is retained.")
        path, source_root = matches[0]
    units = manifest_units(registry, source_root)
    answer = {"units": units, "resolution": "recorded"}
    if not full:
        return answer
    if path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError("The log exceeds the 100 MB full-resolution limit; recorded data is retained.")
    series, count, read_bytes = {}, 0, 0
    with path.open(encoding="utf-8", errors="replace") as log:
        for line in iter(lambda: log.readline(1_000_001), ""):
            read_bytes += len(line.encode("utf-8"))
            if len(line) > 1_000_000 or read_bytes > MAX_LOG_BYTES:
                raise ValueError("The log grew past the full-resolution read limit; recorded data is retained.")
            sample = sample_of(line)
            if sample is None:
                continue
            for metric, value in sample.values.items():
                series.setdefault(metric, []).append([sample.step, value if value is not None and math.isfinite(value) else None])
                count += 1
                if count > MAX_LOG_POINTS:
                    raise ValueError("The log exceeds the one-million-point full-resolution limit; recorded data is retained.")
    return {**answer, "series": series, "resolution": "full"}


def manifest_units(registry: GrantRegistry, source_root: Path | None) -> dict:
    units = {}
    if source_root is not None:
        grant = registry.grant_for_root(source_root)
        manifest = grant.root / "expandrg.json"
        if manifest.is_file():
            try:
                _, text, truncated = registry.read_file(manifest, grant)
                data = json.loads(text) if not truncated else {}
                units = {k: v for k, v in data.get("units", {}).items()
                         if isinstance(k, str) and isinstance(v, str) and len(k) <= 80 and len(v) <= 24}
            except (ValueError, TypeError, AttributeError, AccessDenied):
                pass
    return units
