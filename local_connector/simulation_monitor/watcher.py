"""Follow a growing simulation log and run the anomaly checks on new lines.

Read-only: it opens the log for reading and never signals, pauses or stops
the simulation process. Stopping a watcher only stops the watching.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from local_connector.simulation_monitor.checks import Alert, LogMonitor, MonitorConfig, Sample, sample_of

log = logging.getLogger(__name__)


class LogWatcher:
    def __init__(
        self,
        path: Path,
        on_alerts: Callable[[list[Alert]], None],
        config: MonitorConfig | None = None,
        poll_seconds: float = 1.0,
        missing_after_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        on_samples: Callable[[list[Sample]], None] | None = None,
        on_restart: Callable[[], None] | None = None,
    ):
        self.path = Path(path)
        self.on_alerts = on_alerts
        self.on_samples = on_samples  # step numbers for graphs; only set when the run is shared
        self.on_restart = on_restart  # the log was replaced: a new run started
        self.config = config or MonitorConfig()
        self.poll_seconds = poll_seconds
        self.missing_after_seconds = missing_after_seconds
        self._clock = clock
        self.monitor = LogMonitor(self.config)
        self.alerts: list[Alert] = []
        self.lines_read = 0
        self.state = "watching"
        self._position = 0
        self._buffer = ""
        self._started = clock()
        self._last_growth = self._started
        self._missing_reported = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"watch {self.path.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.state = "stopped"

    def poll_once(self) -> list[Alert]:
        """Read whatever is new and check it. Returns the new alerts."""
        now = self._clock()
        new: list[Alert] = []
        samples: list[Sample] = []
        if not self.path.exists():
            if not self._missing_reported and now - self._started >= self.missing_after_seconds:
                self._missing_reported = True
                new.append(Alert("warning", "missing_output",
                                 f"{self.path.name} has not appeared after {now - self._started:.0f}s."))
        else:
            size = self.path.stat().st_size
            if size < self._position:  # the file was replaced: a new run
                self._position, self._buffer, self.lines_read = 0, "", 0
                self.monitor = LogMonitor(self.config)
                if self.on_restart:
                    self.on_restart()
            if size > self._position:
                with open(self.path, "rb") as f:
                    f.seek(self._position)
                    data = f.read(size - self._position)
                self._position += len(data)
                lines = (self._buffer + data.decode("utf-8", errors="replace")).split("\n")
                self._buffer = lines.pop()  # an unfinished last line waits for its newline
                for line in lines:
                    self.lines_read += 1
                    new += self._read(line, samples)
                self._last_growth = now
            else:
                if self._buffer and now - self._last_growth >= self.poll_seconds:
                    self.lines_read += 1
                    new += self._read(self._buffer, samples)
                    self._buffer = ""
                new += self.monitor.stalled(now - self._last_growth)
        if samples and self.on_samples:
            self.on_samples(samples)
        if new:
            self.alerts += new
            self.on_alerts(new)
        return new

    def _read(self, line: str, samples: list[Sample]) -> list[Alert]:
        sample = sample_of(line)
        if sample:
            samples.append(sample)
        return self.monitor.feed(line, self.lines_read)

    def status(self) -> dict:
        return {
            "file": self.path.name,
            "state": self.state,
            "lines_read": self.lines_read,
            "alerts": len(self.alerts),
            "critical": sum(a.severity == "critical" for a in self.alerts),
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("Watching %s failed", self.path.name)
                self.state = "error"
                return
            self._stop.wait(self.poll_seconds)
