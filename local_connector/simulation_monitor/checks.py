"""Cheap, deterministic anomaly checks on simulation output.

They only read output. ExpandRG never pauses, stops or kills a simulation, even
on a Critical alert, and CREAO is not involved: explanations come later, when
the researcher asks for a diagnosis.

Severities:
- critical: strong evidence of failure (NaN/Inf, overflow-size values, error lines)
- warning: a likely numerical problem (sudden jumps, a residual rising steadily)
- informational: worth a look (a configured threshold crossed, output stalled)
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from backend.monitoring.alerts import CHECKS, SEVERITIES  # noqa: F401  (shared with the backend)
from backend.orchestration.evidence_policy import redact_secrets

_NUMBER = r"[-+]?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|nan|inf(?:inity)?)"
_PAIR = re.compile(rf"([A-Za-z_][\w|\[\]]*)\s*[=:]\s*({_NUMBER})(?![\w.])", re.IGNORECASE)
_NON_FINITE_WORD = re.compile(r"(?<![\w.])[-+]?(nan|inf|infinity)(?![\w.])", re.IGNORECASE)
_ERROR_LINE = re.compile(r"\b(error|traceback|segmentation fault|exception|fatal)\b", re.IGNORECASE)
_STEP = re.compile(r"\b(?:step|iter(?:ation)?|it)\s*[=:]?\s*(\d+)", re.IGNORECASE)
_STEP_NAMES = {"step", "steps", "iter", "iteration", "it", "timestep"}  # counters, not metrics to plot
MAX_LINE_CHARS = 300


@dataclass
class Alert:
    severity: str
    check: str
    message: str
    metric: str | None = None
    line_number: int | None = None
    line: str = ""  # the log line that triggered it, secrets blanked out
    step: int | None = None  # the step the log line reports, if any

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sample:
    """The numbers one log line reports for one step, for graphs. None means NaN or Inf."""

    step: int
    values: dict[str, float | None]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MonitorConfig:
    jump_factor: float = 100.0  # warning when a value grows this many times between two samples
    overflow_magnitude: float = 1e100  # critical when a finite value is this large
    worsening_samples: int = 3  # warning when a residual-like value rises this many times in a row
    worsening_names: tuple[str, ...] = ("residual", "res", "error", "err")
    thresholds: dict[str, tuple[float, str]] = field(default_factory=dict)  # metric -> (max, severity)
    stall_seconds: float = 120.0  # informational when a watched file stops growing this long


class LogMonitor:
    """Feed it log lines in order; it returns any new alerts.

    Each check fires at most once per metric, so a blow-up gives a handful of
    clear alerts instead of one per line.
    """

    def __init__(self, config: MonitorConfig | None = None):
        self.config = config or MonitorConfig()
        self._last: dict[str, float] = {}
        self._rises: dict[str, int] = {}
        self._fired: set[tuple[str, str]] = set()
        self._stalled = False
        self._step: int | None = None

    def feed(self, line: str, line_number: int) -> list[Alert]:
        self._stalled = False
        text = line.rstrip("\r\n")
        alerts: list[Alert] = []
        where = _where(text, line_number)
        self._step = _step_of(text)

        pairs = [(name, _to_float(value)) for name, value in _PAIR.findall(text)]
        named_non_finite = False
        for name, value in pairs:
            if not math.isfinite(value):
                named_non_finite = True
                self._fire(alerts, "critical", "nan_inf", name, line_number, text,
                           f"{name} became {value} {where}: the simulation has produced non-finite numbers.")
                continue
            if abs(value) >= self.config.overflow_magnitude:
                self._fire(alerts, "critical", "overflow", name, line_number, text,
                           f"{name} reached {value:.3g} {where}: overflow-size values, the run is diverging.")
            previous = self._last.get(name)
            if previous not in (None, 0.0) and abs(value) / abs(previous) >= self.config.jump_factor:
                self._fire(alerts, "warning", "sudden_jump", name, line_number, text,
                           f"{name} jumped from {previous:.3g} to {value:.3g} {where} "
                           f"({abs(value) / abs(previous):.3g}x between samples).")
            if _is_residual(name, self.config.worsening_names) and previous is not None:
                self._rises[name] = self._rises.get(name, 0) + 1 if value > previous else 0
                if self._rises[name] >= self.config.worsening_samples:
                    self._fire(alerts, "warning", "worsening", name, line_number, text,
                               f"{name} has risen {self._rises[name]} samples in a row {where}: "
                               "convergence is getting worse.")
            if name in self.config.thresholds:
                limit, severity = self.config.thresholds[name]
                if value > limit:
                    self._fire(alerts, severity, "threshold", name, line_number, text,
                               f"{name} = {value:.3g} {where} is above its configured limit {limit:.3g}.")
            self._last[name] = value

        if not named_non_finite and _NON_FINITE_WORD.search(text):
            self._fire(alerts, "critical", "nan_inf", None, line_number, text,
                       f"Non-finite value (NaN/Inf) reported {where}.")
        if _ERROR_LINE.search(text):
            self._fire(alerts, "critical", "error_line", None, line_number, text, f"Error reported {where}.")
        return alerts

    def stalled(self, seconds_since_last_line: float) -> list[Alert]:
        """Call while watching a file; reports once per pause in output."""
        if self._stalled or seconds_since_last_line < self.config.stall_seconds:
            return []
        self._stalled = True
        return [Alert("informational", "stalled",
                      f"No new output for {seconds_since_last_line:.0f}s. The run may be stuck or finished.")]

    def _fire(self, alerts: list[Alert], severity: str, check: str, metric: str | None,
              line_number: int, line: str, message: str) -> None:
        key = (check, metric or "")
        if key in self._fired:
            return
        self._fired.add(key)
        shown, _ = redact_secrets(line[:MAX_LINE_CHARS])
        alerts.append(Alert(severity, check, message, metric, line_number, shown, self._step))


def sample_of(line: str) -> Sample | None:
    """The step and named numbers on a log line such as "step 200  max|T| = 2.75e+14  residual = 4.7e+14".
    Lines without a step number (headers, errors) give None."""
    step = _step_of(line)
    if step is None:
        return None
    values: dict[str, float | None] = {}
    for name, value in _PAIR.findall(line):
        if name.lower() not in _STEP_NAMES:
            number = _to_float(value)
            values[name] = number if math.isfinite(number) else None
    return Sample(step, values) if values else None


def scan_lines(lines: list[str], config: MonitorConfig | None = None) -> list[Alert]:
    """Run every check over a whole log at once."""
    monitor = LogMonitor(config)
    alerts: list[Alert] = []
    for number, line in enumerate(lines, start=1):
        alerts += monitor.feed(line, number)
    return alerts


def _to_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return math.nan


def _is_residual(name: str, residual_names: tuple[str, ...]) -> bool:
    """Whole words only: "residual" and "res_norm" count, "resolution" doesn't."""
    return any(part in residual_names for part in re.split(r"[^a-z]+", name.lower()))


def _step_of(text: str) -> int | None:
    step = _STEP.search(text)
    return int(step.group(1)) if step else None


def _where(text: str, line_number: int) -> str:
    step = _STEP.search(text)
    return f"at step {step.group(1)} (line {line_number})" if step else f"on line {line_number}"
