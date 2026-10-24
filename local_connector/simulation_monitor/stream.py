"""A run whose output arrives line by line, from a VS Code terminal, instead of
from a log file. The same checks and the same reporting as LogWatcher: the
lines are read here, on this computer, and only the step numbers and problems
go to the backend. Reading never pauses or stops the run."""
from __future__ import annotations

import threading

from local_connector.simulation_monitor.checks import Alert, LogMonitor, MonitorConfig, Sample, sample_of

MAX_LINE_CHARS = 2000


class StreamWatch:
    def __init__(self, poster, config: MonitorConfig | None = None):
        self.poster = poster  # a BackendPoster: alerts, samples, stopped()
        self.monitor = LogMonitor(config)
        self.alerts: list[Alert] = []
        self.lines_read = 0
        self.state = "watching"
        self.exit_code: int | None = None
        self._lock = threading.Lock()

    def feed(self, lines: list[str]) -> list[Alert]:
        """New output lines, in order. Returns the new alerts."""
        new: list[Alert] = []
        samples: list[Sample] = []
        with self._lock:
            if self.state != "watching":
                return []
            for line in lines:
                line = line.rstrip("\r\n")[:MAX_LINE_CHARS]
                self.lines_read += 1
                sample = sample_of(line)
                if sample:
                    samples.append(sample)
                new += self.monitor.feed(line, self.lines_read)
            self.alerts += new
        if samples:
            self.poster.samples(samples)
        if new:
            self.poster(new)
        return new

    def end(self, exit_code: int | None) -> None:
        """The command finished. A non-zero exit code is reported as a Critical problem."""
        with self._lock:
            if self.state != "watching":
                return
            self.state, self.exit_code = "finished", exit_code
            failed = exit_code not in (None, 0)
            if failed:
                alert = Alert("critical", "exit_code", f"The run ended with exit code {exit_code} (a failure).",
                              None, self.lines_read)
                self.alerts.append(alert)
        if failed:
            self.poster([alert])
        self.poster.stopped()

    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "lines_read": self.lines_read, "alerts": len(self.alerts),
                    "critical": sum(a.severity == "critical" for a in self.alerts), "exit_code": self.exit_code}
