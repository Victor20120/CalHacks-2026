"""Local, immutable provenance and a durable metadata-only context outbox.

The existing saved project is the artifact, not a second central repository.
Only a bounded manifest and opaque locators leave this process. Artifact reads
always check the source project's current grant, including after a restart.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.orchestration.evidence_policy import path_problem, redact_secrets
from local_connector.file_access.grants import AccessDenied

MAX_INDEX_FILES = 64
MAX_PENDING_EVENTS = 16
MAX_RECORDS = 100
MAX_ARTIFACT_CHARS = 12_000
MAX_ARTIFACT_BYTES = 5_000_000
_CODE_SUFFIXES = frozenset({
    ".py", ".pyx", ".pxd", ".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cu", ".cuh", ".jl", ".r", ".m",
    ".rs", ".go", ".java", ".js", ".ts", ".sh", ".bat", ".ps1", ".cmake",
})
_CODE_NAMES = frozenset({"makefile", "cmakelists.txt", "pyproject.toml", "setup.py", "setup.cfg",
                         "requirements.txt", "cargo.toml", "expandrg.json"})
_SECRET_NAME = re.compile(r"(?i)(?:password|passwd|secret|token|(?:api|access)[_-]?key|credential)")
_ABSOLUTE_TEXT = re.compile(r"(?:^|[\s=\"'])(?:[A-Za-z]:[\\/]|/(?:[^\s/]+/))")
_REF = re.compile(r"^(?:run|anomaly|investigation|episode|sim|exp|base):[A-Za-z0-9_.-]{1,128}$")
_SHA = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _safe_text(value, limit=240):
    if not isinstance(value, str) or _ABSOLUTE_TEXT.search(value) or value.startswith(("/", "\\")):
        return None
    clean, _ = redact_secrets(value)
    return clean[:limit]


def _safe_parameters(parameters: dict | None) -> tuple[dict, bool]:
    result = {}
    complete = True
    for key, value in list((parameters or {}).items())[:64]:
        if not isinstance(key, str) or len(key) > 80 or _SECRET_NAME.search(key) or not _safe_text(key):
            complete = False
            continue
        if value is None or isinstance(value, (bool, int)):
            result[key] = value
        elif isinstance(value, float) and math.isfinite(value):
            result[key] = value
        elif isinstance(value, str) and (safe := _safe_text(value, 160)) is not None:
            result[key] = safe
        else:
            complete = False
    return result, complete and len(parameters or {}) <= 64


def declared_parameters(root: Path, entry_name: str | None = None) -> tuple[dict, str]:
    """Read only manifest-declared parameters, never infer config keys or defaults.

    Without a selected entry point a simulation is unambiguous only when the
    manifest has one entry. Unknown effective values remain explicitly unknown.
    """
    from local_connector.experiments.manifest import load_manifest
    from local_connector.experiments.workspace import ExperimentRefused, read_parameters

    try:
        entries = load_manifest(root)
        if entry_name:
            entry = entries.get(entry_name)
        else:
            entry = next(iter(entries.values())) if len(entries) == 1 else None
        if entry is None:
            return {}, "not_captured"
        if not entry.config or not entry.parameters:
            return {}, "not_declared"
        parameters = read_parameters(root, entry)
        safe, complete = _safe_parameters(parameters)
        # read_parameters uses None for absent keys; never claim these values are known.
        complete = complete and all(value is not None for value in parameters.values())
        return safe, "complete_declared" if complete else "partial"
    except (OSError, ValueError, ExperimentRefused):
        return {}, "not_captured"


def git_head(root: Path | None) -> str | None:
    """Optional bounded HEAD read; no Git commands, hooks, filters or index writes.

    Worktree indirection and symbolic links are deliberately left unknown. The
    execution digest is authoritative; HEAD does not establish a clean tree.
    """
    if root is None:
        return None
    git = Path(root) / ".git"
    if git.is_symlink() or not git.is_dir():
        return None

    def read(relative: str, limit: int) -> str | None:
        path = git / relative
        if any(p.is_symlink() for p in [path, *path.parents] if p != git.parent and git.parent in p.parents):
            return None
        try:
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
            return data.decode("ascii") if len(data) <= limit else None
        except (OSError, UnicodeError):
            return None

    head = read("HEAD", 512)
    if not head:
        return None
    head = head.strip()
    if _SHA.fullmatch(head):
        return head.lower()
    if not head.startswith("ref: refs/"):
        return None
    ref = head[5:]
    if not re.fullmatch(r"refs/[A-Za-z0-9_./-]{1,200}", ref) or ".." in ref:
        return None
    target = read(ref, 512)
    if target and _SHA.fullmatch(target.strip()):
        return target.strip().lower()
    packed = read("packed-refs", 1_000_000)
    if packed:
        for line in packed.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and _SHA.fullmatch(parts[0]):
                return parts[0].lower()
    return None


def capture_snapshot(root: Path, *, original_root: Path | None = None,
                     parameters: dict | None = None, parameter_completeness: str = "not_captured") -> dict:
    """Hash the retained execution copy once; keep a bounded per-file digest index.

    All files contribute to the aggregate digest, even when their per-file index
    is truncated. Code/input classification is by file type, not a claim about
    what the solver actually reads. Their union is the exact execution snapshot.
    """
    root = Path(root)
    total, code, inputs = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    count = code_count = input_count = 0
    index = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path_problem(relative):
            continue
        digest = _digest(path)
        raw = bytes.fromhex(digest)
        total.update(relative.encode())
        total.update(raw)
        is_code = path.suffix.lower() in _CODE_SUFFIXES or path.name.lower() in _CODE_NAMES
        part = code if is_code else inputs
        part.update(relative.encode())
        part.update(raw)
        count += 1
        code_count += int(is_code)
        input_count += int(not is_code)
        if len(index) < MAX_INDEX_FILES and len(relative) <= 240 and _safe_text(relative) == relative:
            index.append({"relative_path": relative, "digest": digest, "kind": "code" if is_code else "input",
                          "bytes": path.stat().st_size})
    safe_parameters, complete = _safe_parameters(parameters)
    if not complete and parameter_completeness == "complete_declared":
        parameter_completeness = "partial"
    return {"snapshot_fingerprint": total.hexdigest(), "code_fingerprint": code.hexdigest(),
            "input_fingerprint": inputs.hexdigest(), "file_count": count, "code_file_count": code_count,
            "input_file_count": input_count, "file_index": index, "file_index_truncated": len(index) < count,
            "parameters": safe_parameters, "parameter_completeness": parameter_completeness,
            "git_commit": git_head(original_root), "git_dirty": None, "git_state_completeness": "not_captured",
            "captured_at": _now()}


def atomic_save(path: Path, record: dict) -> None:
    """Atomically replace only connector-owned JSON, including its pending events."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _typed(value, prefix: str):
    if value is None:
        return None
    value = str(value)
    value = value if ":" in value else f"{prefix}:{value}"
    return value if value.startswith(prefix + ":") and _REF.fullmatch(value) else None


def _payload(record: dict, kind: str) -> dict:
    prefix = "sim" if kind == "simulation" else "exp"
    ref = f"{prefix}:{record['id']}"
    capture = record.get("research_context", {}).get("capture")
    metadata = copy.deepcopy(capture) if capture else {"snapshot_fingerprint": None,
        "parameters": {}, "parameter_completeness": "not_captured", "git_commit": None,
        "git_dirty": None, "git_state_completeness": "not_captured"}
    metadata.update({"status": record.get("status"), "created_at": record.get("created_at"),
                     "started_at": record.get("started_at"), "finished_at": record.get("finished_at"),
                     "exit_code": record.get("exit_code"), "seconds": record.get("seconds"),
                     "origin_run_ref": _typed(record.get("origin_run_ref"), "run"),
                     "origin_anomaly_ref": _typed(record.get("origin_anomaly_ref"), "anomaly"),
                     "origin_investigation_ref": _typed(record.get("origin_investigation_ref"), "investigation"),
                     "backend_run_ref": _typed(record.get("backend_run_id"), "run"),
                     "parent_ref": _typed(record.get("parent_simulation_id"), "sim") if kind == "simulation" else None})
    if kind == "experiment":
        metadata["set_id"] = _safe_text(record.get("set_id"), 128)
        metadata["baseline_ref"] = _typed(record.get("set_id"), "base")
        baseline = record.get("baseline")
        if isinstance(baseline, str) and not path_problem(baseline) and capture:
            indexed = next((item for item in capture.get("file_index", []) if item["relative_path"] == baseline), None)
            if indexed:
                metadata["baseline_artifact"] = {"relative_path": baseline, "digest": indexed["digest"]}
    artifacts = []
    if capture:
        for role, artifact_kind, digest_key in (("snapshot", "execution_snapshot", "snapshot_fingerprint"),
                                               ("code", "code_version", "code_fingerprint"),
                                               ("inputs", "input_manifest", "input_fingerprint")):
            artifacts.append({"kind": artifact_kind, "source_key": f"{ref}:{role}", "digest": capture[digest_key],
                              "metadata": {"captured_at": capture["captured_at"], "file_count": capture["file_count"]},
                              "locator": {"record_ref": ref, "role": role}})
    output = record.get("research_context", {}).get("output")
    if output:
        artifacts.append({"kind": "output_artifact", "source_key": f"{ref}:output", "digest": output["digest"],
                          "metadata": {"captured_at": output["captured_at"], "bytes": output["bytes"]},
                          "locator": {"record_ref": ref, "role": "output", "relative_path": "output.log"}})
    return {"ref": ref, "kind": kind, "project_id": record["project_id"], "owner_id": record["member_id"],
            "metadata": metadata, "artifacts": artifacts}


def update_record(record: dict, kind: str, folder: Path) -> None:
    """Capture state transitions, without rehashing the snapshot on log updates.

    Events are full-state projections. Once the bounded outbox fills, an older
    unacknowledged projection can be superseded by a newer complete projection;
    the latest state remains durable and its monotonic revision is never reused.
    """
    context = record.setdefault("research_context", {})
    output = Path(folder) / "output.log"
    if record.get("status") not in ("queued", "running", "prepared", "ready", "awaiting_setup", "needs_input"):
        if not context.get("output") and output.is_file() and not output.is_symlink():
            context["output"] = {"digest": _digest(output), "bytes": output.stat().st_size, "captured_at": _now()}
    payload = _payload(record, kind)
    fingerprint = _json_hash(payload)
    if context.get("last_payload_hash") == fingerprint:
        return
    revision = int(context.get("revision", 0)) + 1
    occurred = _now()
    context.update(revision=revision, updated_at=occurred, last_payload_hash=fingerprint)
    payload.update(source_revision=revision, updated_at=occurred)
    event = {"producer_id": f"connector:{payload['ref']}", "event_id": f"{payload['ref']}:{revision}",
             "schema_version": 1, "event_type": f"{kind}.updated", "subject_ref": payload["ref"],
             "source_revision": revision, "project_id": record["project_id"], "owner_id": record["member_id"],
             "occurred_at": occurred, "payload": payload}
    context["pending_events"] = [*context.get("pending_events", [])[-(MAX_PENDING_EVENTS - 1):], event]


def _managers(sim_manager, experiment_manager):
    if sim_manager is not None:
        yield sim_manager, "simulation", sim_manager.lock, sim_manager.records
    if experiment_manager is not None:
        yield experiment_manager, "experiment", experiment_manager._lock, experiment_manager._records


def export_records(sim_manager, experiment_manager, member_id: str, project_id: str, limit: int = 50,
                   record_ref: str | None = None) -> dict:
    """Return owner-scoped metadata; legacy records keep unknown provenance."""
    limit = max(1, min(int(limit), MAX_RECORDS))
    candidates = []
    for manager, kind, lock, records in _managers(sim_manager, experiment_manager):
        with lock:
            candidates.extend((copy.deepcopy(record), kind) for record in records.values()
                              if record.get("member_id") == member_id and record.get("project_id") == project_id)
    candidates.sort(key=lambda item: item[0].get("created_at", ""), reverse=True)
    if record_ref:
        def matches(item):
            record, kind = item
            prefix = "sim" if kind == "simulation" else "exp"
            return (record_ref == f"{prefix}:{record['id']}" or
                    record_ref == _typed(record.get("set_id"), "base") or
                    record_ref == _typed(record.get("backend_run_id"), "run") or
                    any(record_ref == _typed(record.get(key), prefix_) for key, prefix_ in (
                        ("origin_run_ref", "run"), ("origin_anomaly_ref", "anomaly"),
                        ("origin_investigation_ref", "investigation"))))
        candidates = [item for item in candidates if matches(item)]
    exported, events = [], []
    for record, kind in candidates[:limit]:
        context = record.get("research_context", {})
        payload = _payload(record, kind)
        payload.update(source_revision=context.get("revision", 0),
                       updated_at=context.get("updated_at", record.get("created_at")))
        # Events contain complete metadata projections. Keep the newest pending
        # projection for each record in this bounded delivery; older revisions
        # remain in the durable outbox until individually acknowledged.
        pending = context.get("pending_events", [])
        selected = pending[-1:] if pending else []
        trial = {"records": [*exported, payload], "events": [*events, *selected]}
        if len(json.dumps(trial, ensure_ascii=True).encode("utf-8")) > 700_000:
            break
        exported.append(payload)
        events.extend(selected)
    return {"records": exported, "events": events, "truncated": len(candidates) > len(exported)}


def acknowledge_events(sim_manager, experiment_manager, member_id: str, project_id: str, event_ids: list[str]) -> dict:
    """Acknowledge exact events atomically without generating another event."""
    identifiers = set(event_ids[: MAX_RECORDS * MAX_PENDING_EVENTS])
    acknowledged = []
    for manager, kind, lock, records in _managers(sim_manager, experiment_manager):
        with lock:
            for record in records.values():
                if record.get("member_id") != member_id or record.get("project_id") != project_id:
                    continue
                context = record.get("research_context", {})
                pending = context.get("pending_events", [])
                matched = [event["event_id"] for event in pending if event["event_id"] in identifiers]
                if matched:
                    context["pending_events"] = [event for event in pending if event["event_id"] not in identifiers]
                    try:
                        atomic_save(manager.home / record["id"] / f"{kind}.json", record)
                    except Exception:
                        context["pending_events"] = pending
                        raise
                    acknowledged.extend(matched)
    return {"acknowledged": acknowledged}


def read_artifact(sim_manager, experiment_manager, member_id: str, project_id: str, record_ref: str,
                  role: str, relative_path: str, registry, max_chars: int = MAX_ARTIFACT_CHARS) -> dict:
    """Read bounded, redacted immutable evidence under the current source grant.

    The backend supplies authenticated owner/project, never an arbitrary path.
    Missing, changed, binary or revoked evidence is unavailable, not substituted
    with a current project file.
    """
    if not isinstance(record_ref, str) or not _REF.fullmatch(record_ref) or record_ref.split(":", 1)[0] not in ("sim", "exp"):
        raise AccessDenied("No authorized saved artifact.")
    prefix, identifier = record_ref.split(":", 1)
    manager = sim_manager if prefix == "sim" else experiment_manager
    if manager is None:
        raise AccessDenied("No authorized saved artifact.")
    lock = manager.lock if prefix == "sim" else manager._lock
    with lock:
        records = manager.records if prefix == "sim" else manager._records
        record = copy.deepcopy(records.get(identifier, {}))
    if record.get("member_id") != member_id or record.get("project_id") != project_id:
        raise AccessDenied("No authorized saved artifact.")
    original = record.get("runtime", {}).get("source_root") if prefix == "sim" else record.get("root")
    if not original:
        raise AccessDenied("The source grant was not captured for this artifact.")
    registry.grant_for_root(original)  # a retained copy never overrides revocation
    context = record.get("research_context", {})
    if role == "output":
        if relative_path != "output.log" or not context.get("output"):
            raise AccessDenied("No captured output artifact.")
        root = manager.home / identifier
        expected = context["output"]["digest"]
    elif role in ("snapshot", "code", "inputs"):
        capture = context.get("capture", {})
        indexed = next((item for item in capture.get("file_index", []) if item["relative_path"] == relative_path), None)
        if not indexed or (role == "code" and indexed["kind"] != "code") or (role == "inputs" and indexed["kind"] != "input"):
            raise AccessDenied("The file is not in the captured digest index.")
        root = manager.home / identifier / ("snapshot" if prefix == "sim" else "project")
        expected = indexed["digest"]
    else:
        raise AccessDenied("Unknown saved artifact role.")
    if path_problem(relative_path):
        raise AccessDenied("The artifact path is not allowed.")
    path = root / relative_path
    resolved_root, resolved = root.resolve(), path.resolve()
    if not resolved.is_relative_to(resolved_root) or path.is_symlink() or not resolved.is_file():
        raise AccessDenied("The saved artifact is unavailable.")
    if any(part.is_symlink() for part in path.parents if root in part.parents):
        raise AccessDenied("The saved artifact is unavailable.")
    if resolved.stat().st_size > MAX_ARTIFACT_BYTES:
        raise AccessDenied("The saved artifact exceeds the bounded evidence read limit.")
    data = resolved.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise AccessDenied("The saved artifact changed after capture.")
    if b"\0" in data[:8192]:
        raise AccessDenied("The saved artifact is binary.")
    text = redact_secrets(data.decode("utf-8", errors="replace"))[0]
    # Absolute paths in solver output/source are not sent as evidence.
    text = re.sub(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s\"'<>]+|/(?:[^\s/]+/)+[^\s\"'<>]*)", "[local path]", text)
    limit = max(1, min(int(max_chars), MAX_ARTIFACT_CHARS))
    return {"record_ref": record_ref, "role": role, "relative_path": relative_path, "digest": expected,
            "text": text[:limit], "truncated": len(text) > limit}
