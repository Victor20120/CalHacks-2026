"""Run one experiment command as a limited local process.

Limits: wall time, memory and output size, plus a stop button. On Windows the
process runs in a Job Object (a memory cap, and stopping it stops everything it
started); elsewhere it gets resource limits and its own process group. The
environment is cleaned of keys and tokens. A plain process is not cut off from
the network or from other folders; that is acceptable only because the
researcher approves every run (Documents/agent-runtime-design.md, decision 1).
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from local_connector.experiments.manifest import Limits

MB = 1024 * 1024
POLL_SECONDS = 0.2
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.IGNORECASE)
_OUT_OF_MEMORY = (b"MemoryError", b"Cannot allocate memory", b"bad_alloc", b"Out of memory", b"out of memory")


@dataclass
class Outcome:
    status: str  # "finished", "failed" or "stopped"
    reason: str  # why it stopped or failed, for people
    exit_code: int | None
    seconds: float


def clean_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The parent's environment without anything that looks like a credential, and
    without ExpandRG's own settings."""
    environ = dict(os.environ if environ is None else environ)
    return {k: v for k, v in environ.items()
            if not _SECRET_NAME.search(k) and not k.upper().startswith(("CREAO_", "EXPANDRG_"))}


def resolve_command(command: tuple[str, ...], cwd: Path) -> list[str]:
    """{python} becomes this Python; a program inside the copy gets its full path,
    because Windows looks up a relative program from the parent's folder."""
    words = [sys.executable if word == "{python}" else word for word in command]
    first = Path(cwd) / words[0]
    if not Path(words[0]).is_absolute() and first.is_file():
        words[0] = str(first)
    return words


def run_limited(command: list[str], cwd: Path, log_path: Path, limits: Limits, cancel: threading.Event) -> Outcome:
    started = time.monotonic()
    job = None
    with open(log_path, "wb") as out:
        options = {"cwd": str(cwd), "stdout": out, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
                   "env": clean_environment()}
        try:
            if os.name == "nt":
                job = _WindowsJob(limits.memory_mb)
                process = subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, **options)
                job.assign(process)
            else:
                process = subprocess.Popen(command, start_new_session=True,
                                           preexec_fn=lambda: _posix_limits(limits), **options)
        except OSError as exc:
            if job:
                job.close()
            return Outcome("failed", f"Could not start the command: {exc}", None, 0.0)

        reason = None
        while process.poll() is None:
            if cancel.is_set():
                reason = "Stopped by you."
            elif time.monotonic() - started > limits.wall_seconds:
                reason = f"Stopped: it ran longer than its {limits.wall_seconds} s time limit."
            elif log_path.stat().st_size > limits.output_mb * MB:
                reason = f"Stopped: it wrote more than its {limits.output_mb} MB output limit."
            if reason:
                _stop(process, job)
                break
            time.sleep(POLL_SECONDS)
        exit_code = process.wait()
    peak_mb = job.peak_mb() if job else None
    if job:
        job.close()
    seconds = round(time.monotonic() - started, 2)
    if reason:
        return Outcome("stopped", reason, exit_code, seconds)
    if exit_code != 0 and _ran_out_of_memory(log_path, peak_mb, limits):
        return Outcome("stopped", f"Stopped: it needed more than its {limits.memory_mb} MB memory limit.", exit_code, seconds)
    if exit_code != 0:
        return Outcome("failed", f"The command exited with code {exit_code}.", exit_code, seconds)
    return Outcome("finished", "", exit_code, seconds)


def _ran_out_of_memory(log_path: Path, peak_mb: float | None, limits: Limits) -> bool:
    if peak_mb is not None and peak_mb >= 0.9 * limits.memory_mb:
        return True
    with open(log_path, "rb") as f:
        f.seek(max(0, log_path.stat().st_size - 4096))
        tail = f.read()
    return any(marker in tail for marker in _OUT_OF_MEMORY)


def _stop(process: subprocess.Popen, job: "_WindowsJob | None") -> None:
    """Stop the process and everything it started."""
    try:
        if job:
            job.terminate()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def _posix_limits(limits: Limits) -> None:  # runs in the child, before the command
    import resource

    memory = limits.memory_mb * MB
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    cpu = limits.wall_seconds + 5
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))


class _WindowsJob:
    """A Windows Job Object: caps the memory of everything in it, and closing or
    terminating it ends every process inside."""

    def __init__(self, memory_mb: int):
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMITS), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._ctypes, self._info_type = ctypes, EXTENDED_LIMITS
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self._kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Could not create a Windows Job Object.")
        info = EXTENDED_LIMITS()
        job_memory, kill_on_close = 0x200, 0x2000
        info.BasicLimitInformation.LimitFlags = job_memory | kill_on_close
        info.JobMemoryLimit = memory_mb * MB
        extended_limit_information = 9
        if not self._kernel32.SetInformationJobObject(self.handle, extended_limit_information,
                                                      ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "Could not set the Job Object's limits.")

    def assign(self, process: subprocess.Popen) -> None:
        if not self._kernel32.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            raise OSError(self._ctypes.get_last_error(), "Could not put the process in its Job Object.")

    def peak_mb(self) -> float | None:
        info = self._info_type()
        if not self._kernel32.QueryInformationJobObject(self.handle, 9, self._ctypes.byref(info),
                                                        self._ctypes.sizeof(info), None):
            return None
        return info.PeakJobMemoryUsed / MB

    def terminate(self) -> None:
        self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None
