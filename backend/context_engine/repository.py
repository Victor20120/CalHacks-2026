"""Durable, private-by-default provenance. No scientific claims are inferred."""
from __future__ import annotations

import hashlib
from contextlib import closing
from uuid import uuid4

from backend.auth.permissions import require_project_member
from .models import ContextConflict, ContextError, PRIVATE_PREFIXES, canonical, json_load, now, safe_locator, sanitize, split_ref


def _artifact(conn, owner_id, project_id, kind, source_key, metadata, locator=None, digest=None):
    if not isinstance(kind, str) or not kind or len(kind) > 80 or not isinstance(source_key, str) or not source_key or len(source_key) > 240:
        raise ContextError("An artifact needs a bounded kind and source key.")
    old = conn.execute("SELECT * FROM context_artifacts WHERE project_id=? AND owner_id IS ? AND kind=? AND source_key=?", (project_id, owner_id, kind, source_key)).fetchone()
    stamp = now()
    artifact_id = old["id"] if old else str(uuid4())
    metadata = sanitize(metadata if isinstance(metadata, dict) else {})
    locator = safe_locator(locator)
    digest = digest or metadata.get("digest") or metadata.get("sha256")
    if not isinstance(digest, str) or len(digest) > 160:
        digest = None
    if old and kind not in {"simulation", "experiment", "baseline"} and old["digest"] and digest != old["digest"]:
        raise ContextConflict("Captured evidence is immutable; use a new source key for new content.")
    conn.execute("""INSERT INTO context_artifacts(id,project_id,owner_id,kind,source_key,metadata,locator,digest,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,
        locator=excluded.locator,digest=excluded.digest,updated_at=excluded.updated_at""",
        (artifact_id, project_id, owner_id, kind, source_key, canonical(metadata), canonical(locator) if locator else None, digest, old["created_at"] if old else stamp, stamp))
    return {"ref": "artifact:" + artifact_id, "id": artifact_id, "kind": kind, "project_id": project_id, "owner_id": owner_id, "metadata": metadata, "locator": locator, "digest": digest}


def store_artifact(db, member_id, project_id, kind, source_key, metadata, locator=None):
    """Store an approved evidence descriptor in its owner's private database."""
    if not member_id:
        raise ContextError("Captured artifacts require a private owner.")
    with closing(db.shared()) as shared:
        require_project_member(shared, member_id, project_id)
    with closing(db.private(member_id)) as conn:
        result = _artifact(conn, member_id, project_id, kind, source_key, metadata, locator)
        conn.commit()
        return result


def _link(conn, project_id, source_ref, predicate, target_ref, origin="service_event", status="recorded", evidence_refs=None, provider_run_id=None):
    split_ref(source_ref)
    split_ref(target_ref)
    if not isinstance(predicate, str) or not predicate or len(predicate) > 64 or not all(c.islower() or c == "_" for c in predicate):
        raise ContextError("A relationship needs a bounded predicate.")
    if status not in {"recorded", "proposed", "accepted", "rejected"}:
        raise ContextError("A relationship status cannot verify a scientific claim.")
    refs = list(dict.fromkeys(evidence_refs or []))
    if len(refs) > 20:
        raise ContextError("At most twenty evidence references are accepted.")
    for ref in refs:
        split_ref(ref)
    old = conn.execute("SELECT * FROM context_links WHERE project_id=? AND source_ref=? AND predicate=? AND target_ref=?", (project_id, source_ref, predicate, target_ref)).fetchone()
    # A replay/proposal cannot erase a reviewed relationship or its original provenance.
    if old:
        return _link_row(old)
    stamp = now()
    identifier = str(uuid4())
    conn.execute("INSERT INTO context_links VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identifier, project_id, source_ref, predicate, target_ref, origin, status, canonical(refs), provider_run_id, stamp, stamp))
    return {"id": identifier, "project_id": project_id, "source_ref": source_ref, "predicate": predicate, "target_ref": target_ref, "origin": origin, "status": status, "evidence_refs": refs, "provider_run_id": provider_run_id, "created_at": stamp}


def _link_row(row):
    value = dict(row)
    value["evidence_refs"] = json_load(value["evidence_refs"], [])
    return value


def link(db, member_id, project_id, source_ref, predicate, target_ref, origin="service_event", status="recorded", evidence_refs=None, provider_run_id=None):
    """Trusted service hook. Private endpoints force private storage."""
    source_kind, _ = split_ref(source_ref)
    target_kind, _ = split_ref(target_ref)
    if member_id is None and ({source_kind, target_kind} & PRIVATE_PREFIXES):
        raise ContextError("Private relationships require an owner.")
    if member_id:
        with closing(db.shared()) as shared:
            require_project_member(shared, member_id, project_id)
    with closing(db.private(member_id) if member_id else db.shared()) as conn:
        result = _link(conn, project_id, source_ref, predicate, target_ref, origin, status, evidence_refs, provider_run_id)
        conn.commit()
        return result


def register_investigation(db, member_id, project_id, episode_id, run_id, alert_id, evidence_refs, provider_run_id):
    """Called after the existing private episode save; safe to replay/reconcile."""
    source = "investigation:" + episode_id
    if run_id:
        link(db, member_id, project_id, source, "investigates", run_id if run_id.startswith("run:") else "run:" + run_id, provider_run_id=provider_run_id)
    if alert_id:
        link(db, member_id, project_id, source, "investigates", alert_id if alert_id.startswith("anomaly:") else "anomaly:" + alert_id, provider_run_id=provider_run_id)
    for ref in evidence_refs or []:
        link(db, member_id, project_id, source, "cites", ref, provider_run_id=provider_run_id)
