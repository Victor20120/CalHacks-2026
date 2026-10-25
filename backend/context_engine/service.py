"""Permission-aware projections over the existing research stores.

The engine does no reasoning, file reading, simulation execution or publishing.
Every returned reference is resolved afresh against its authoritative store.
"""
from __future__ import annotations

import base64
import hashlib
import math
from contextlib import closing

from backend.auth.permissions import context_project_ids
from .models import (ContextConflict, ContextError, ContextNotFound, canonical,
                     json_load, now, safe_text, sanitize, split_ref)
from .repository import _artifact, _link, _link_row

MAX_CHARS = 12_000
MAX_RECORDS = 60
_LOCAL_KINDS = {"sim": "simulation", "exp": "experiment", "base": "baseline"}
_SECTIONS = {
    "runs": {"run", "simulation"}, "parameters": {"simulation", "experiment", "baseline"},
    "code_versions": {"code_version", "execution_snapshot"}, "inputs": {"input_manifest"},
    "results": {"run", "experiment", "baseline", "output_artifact"}, "anomalies": {"anomaly"},
    "investigations": {"investigation"}, "evidence": {"evidence", "selected_evidence", "output_artifact"},
    "findings": {"finding"}, "graphs": {"graph"},
    "worksheets": {"worksheet"},
}


class ContextEngine:
    def __init__(self, db, member, connector=None, purpose="private_analysis", project_id=None):
        if purpose not in {"private_analysis", "project_publication"}:
            raise ContextError("Unknown context purpose.")
        self.db, self.member = db, dict(member)
        self.connector, self.purpose, self.project_id = connector, purpose, project_id
        self._connector_state = "offline" if connector is None else "not_requested"
        self._source_truncated = False

    def _active(self, shared):
        row = shared.execute("SELECT * FROM members WHERE id=? AND status='active'", (self.member["id"],)).fetchone()
        if not row:
            raise ContextNotFound("Research context was not found.")
        return dict(row)

    def _project(self, shared, project_id):
        member = self._active(shared)
        row = shared.execute("""SELECT p.* FROM projects p JOIN project_members pm ON pm.project_id=p.id
            WHERE p.id=? AND pm.member_id=? AND p.workspace_id=?""", (project_id, member["id"], member["workspace_id"])).fetchone()
        if not row:
            raise ContextNotFound("Research context was not found.")
        if self.purpose == "project_publication" and self.project_id and project_id not in context_project_ids(shared, self.project_id):
            raise ContextNotFound("Research context was not found.")
        return dict(row), member

    def _envelope(self, shared, ref, kind, row, summary, *, private=False, state="observation", native=None, provenance=None, completeness=None):
        project, member = self._project(shared, row["project_id"])
        if self.project_id and row["project_id"] != self.project_id and kind != "finding":
            raise ContextNotFound("Research context was not found.")
        if private and self.purpose == "project_publication":
            raise ContextNotFound("Research context was not found.")
        value = {
            "ref": ref, "kind": kind, "workspace_id": project["workspace_id"], "project_id": project["id"],
            "owner_id": row.get("owner_id") or row.get("member_id") or (member["id"] if private else row.get("contributed_by")),
            "scope": "private" if private else ("run_owner_or_project_leader" if kind in {"run", "anomaly"} else "project"),
            "created_at": row.get("created_at") or row.get("started_at"), "updated_at": row.get("updated_at") or row.get("created_at"),
            "native_status": native, "scientific_state": state, "source_revision": row.get("source_revision"),
            "provenance": sanitize(provenance or {}), "completeness": completeness or {},
            "authorized_summary": sanitize(summary),
        }
        return value

    def _resolve(self, ref, shared, private):
        kind, identifier = split_ref(ref)
        if kind == "run":
            found = shared.execute("SELECT * FROM simulation_runs WHERE id=?", (identifier,)).fetchone()
            if found:
                row = dict(found)
                _, actor = self._project(shared, row["project_id"])
                if row["member_id"] != actor["id"] and actor["role"] != "leader":
                    found = None
            if not found:
                raise ContextNotFound("Research context was not found.")
            # Summaries use saved numeric points; no live-file inference or replayed LLM work.
            metrics = {}
            for metric in shared.execute("""SELECT metric,COUNT(*) AS points,COUNT(value) AS finite,MIN(step) AS first_step,
                    MAX(step) AS last_step,MIN(value) AS min,MAX(value) AS max
                    FROM run_points WHERE run_id=? GROUP BY metric ORDER BY metric LIMIT 6""", (identifier,)):
                last = shared.execute("SELECT value FROM run_points WHERE run_id=? AND metric=? ORDER BY step DESC,rowid DESC LIMIT 1", (identifier, metric["metric"])).fetchone()
                metrics[metric["metric"]] = {**{key: metric[key] for key in ("points", "finite", "first_step", "last_step", "min", "max")},
                    "last": last[0] if last else None, "retention": "recorded_points_may_be_thinned"}
            return self._envelope(shared, ref, "run", row,
                {"label": row["run_label"], "watching": bool(row["watching"]), "last_step": row["last_step"], "metrics": metrics},
                native="watching" if row["watching"] else "not_watching",
                provenance={"store": "simulation_runs", "simulation_run_ref": ref},
                completeness={"parameters": "not_captured", "code_version": "not_captured", "git_commit": "unknown", "process_status": "unknown"})
        if kind == "anomaly":
            found = shared.execute("SELECT * FROM run_alerts WHERE id=?", (identifier,)).fetchone()
            if not found:
                raise ContextNotFound("Research context was not found.")
            row = dict(found)
            run = self._resolve("run:" + row["run_id"], shared, private)
            row["project_id"], row["member_id"] = run["project_id"], run["owner_id"]
            return self._envelope(shared, ref, "anomaly", row,
                {key: row[key] for key in ("severity", "check_name", "message", "metric", "step", "line_number", "line")},
                native=row["severity"], provenance={"store": "run_alerts", "simulation_run_ref": run["ref"]})
        if kind == "finding":
            found = shared.execute("SELECT * FROM shared_findings WHERE id=?", (identifier,)).fetchone()
            if not found:
                raise ContextNotFound("Research context was not found.")
            row = dict(found)
            self._project(shared, row["project_id"])
            # Comments are a human conversation; only the explicit agent-readable notes are selected.
            notes = [dict(n) for n in shared.execute("SELECT kind,text,member_id,created_at FROM finding_notes WHERE finding_id=? ORDER BY created_at,id LIMIT 10", (identifier,))]
            provenance = {"store": "shared_findings", "approval_id": row["approval_id"], "provider_run_id": row["source_creao_run_id"], "publication_is_verification": False}
            automatic = shared.execute("SELECT 1 FROM auto_shares WHERE finding_id=?", (identifier,)).fetchone()
            if automatic:
                provenance["publication"] = "automatic_hypothesis"
            return self._envelope(shared, ref, "finding", row,
                {"title": row["title"], "summary": row["summary"], "category": row["category"], "notes": notes},
                state="hypothesis" if automatic else row["verification_status"], native=row["verification_status"], provenance=provenance)
        if kind == "worksheet":
            found = shared.execute("""SELECT w.*,ww.cancelled_at AS batch_cancelled_at,wiw.cancelled_at AS sheet_cancelled_at
                FROM worksheets w LEFT JOIN worksheet_in_week wiw ON wiw.worksheet_id=w.id
                LEFT JOIN worksheet_weeks ww ON ww.id=wiw.week_id WHERE w.id=?""", (identifier,)).fetchone()
            if not found:
                raise ContextNotFound("Research context was not found.")
            row = dict(found)
            _, actor = self._project(shared, row["project_id"])
            if actor["role"] != "leader" and (row["member_id"] != actor["id"] or row["batch_cancelled_at"] or row["sheet_cancelled_at"]):
                raise ContextNotFound("Research context was not found.")
            # Reuse task identity/status and teaching references. Human answers,
            # leader comments, and unreleased model answers are never context.
            items = [{"kind": item["kind"], "topic": item["topic"], "finding_refs": ["finding:" + fid for fid in json_load(item["finding_ids"], []) if isinstance(fid, str)]}
                for item in shared.execute("SELECT kind,topic,finding_ids FROM worksheet_items WHERE worksheet_id=? ORDER BY position LIMIT 8", (identifier,))]
            return self._envelope(shared, ref, "worksheet", row, {"title": row["title"], "status": row["status"], "items": items},
                private=True, native=row["status"], provenance={"store": "worksheets", "provider_run_id": row["creao_run_id"]})
        table = {"investigation": "private_episodes", "draft": "share_drafts", "graph": "visualizations"}.get(kind)
        if table:
            found = private.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
            if not found:
                raise ContextNotFound("Research context was not found.")
            row = dict(found)
            self._project(shared, row["project_id"])
            if kind == "investigation":
                details = json_load(row["details"])
                allowed = ("symptom", "reply", "issues", "context_finding_ids", "source_run_ref", "simulation_run_ref", "source_alert_ref", "anomaly_ref", "evidence_refs", "diagnostic_tests", "outcome")
                summary = {"summary": row["summary"], "details": {key: details[key] for key in allowed if key in details}}
                if not details and row["details"]:
                    summary["legacy_details"] = safe_text(row["details"], 1600)
                state = "tested_hypothesis" if details.get("diagnostic_tests") and details.get("outcome") else "hypothesis"
                provenance = {"store": table, "provider_run_id": row["creao_run_id"], "provider_execution_is_simulation": False}
                provenance.update({key: details[key] for key in ("simulation_run_ref", "anomaly_ref", "evidence_refs") if key in details})
            elif kind == "draft":
                summary = {key: row[key] for key in ("title", "summary", "category", "verification_status")}
                state, provenance = "hypothesis", {"store": table, "provider_run_id": row["source_creao_run_id"]}
            else:
                spec = json_load(row["spec"])
                summary = {"title": row["title"], "spec": spec, "fingerprints": json_load(row["fingerprints"])}
                state, provenance = "observation", {"store": table}
            return self._envelope(shared, ref, kind, row, summary, private=True, state=state,
                native=row.get("status"), provenance=provenance, completeness={"source_links": "recorded_or_legacy_projection"})
        if kind in _LOCAL_KINDS or kind == "artifact":
            if kind == "artifact":
                found = private.execute("SELECT * FROM context_artifacts WHERE id=? AND owner_id=?", (identifier, self.member["id"])).fetchone()
            else:
                found = private.execute("SELECT * FROM context_artifacts WHERE source_key=? AND owner_id=? AND kind=?", ("record:" + ref, self.member["id"], _LOCAL_KINDS[kind])).fetchone()
            if not found:
                raise ContextNotFound("Research context was not found.")
            row = dict(found)
            self._project(shared, row["project_id"])
            metadata = json_load(row["metadata"])
            row["source_revision"] = metadata.get("source_revision")
            summary = {key: val for key, val in metadata.items() if not key.startswith("_")}
            summary.update({"digest": row["digest"], "content_access": "requires_current_connector_grant", "connector": self._connector_state})
            if row["locator"]:
                summary["locator"] = json_load(row["locator"])
            # Local observation facts are not unpublished private investigation prose.
            private_payload = kind == "artifact" or row["kind"] not in {"simulation", "experiment", "baseline"}
            return self._envelope(shared, ref, row["kind"], row, summary, private=private_payload or self.purpose != "project_publication",
                state="observation", native=metadata.get("status"),
                provenance={"store": "local_connector", "captured_at": metadata.get("updated_at"), "source_ref": metadata.get("canonical_ref", ref)},
                completeness={"parameters": metadata.get("parameter_completeness", "not_captured"), "git_commit": "captured" if metadata.get("git_commit") else "unknown", "local_content": "not_loaded"})
        raise ContextNotFound("Research context was not found.")

    def _refresh(self, project_id, ref=None):
        if self.connector is None:
            self._connector_state = "offline"
            return
        try:
            params = {"project_id": project_id}
            if ref and split_ref(ref)[0] in {"run", "sim", "exp", "base", "anomaly", "investigation"}:
                params["record_ref"] = ref
            answer = self.connector.get("/context/records", params)
            if not isinstance(answer, dict) or not isinstance(answer.get("records", []), list):
                raise ContextError("Malformed connector context response.")
            receipt = self.ingest_local(project_id, answer)
            self._source_truncated = bool(answer.get("truncated"))
            self._connector_state = "online"
            event_ids = [entry["event_id"] for entry in receipt["acknowledged_event_ids"] if entry["producer_id"] != "local-context-records"]
            if event_ids and hasattr(self.connector, "post"):
                try:
                    self.connector.post("/context/events/ack", {"project_id": project_id, "event_ids": event_ids})
                except Exception:
                    pass  # replay after a lost ACK is safe; committed facts remain online
        except Exception:
            # Stored facts remain usable offline. Never repeat exception strings
            # from a connector, which can contain filesystem paths or credentials.
            self._connector_state = "unavailable"

    def _legacy_links(self, shared, private, ref, project_id):
        """Project existing explicit identifiers only; never infer from labels."""
        edges = []
        def add(source, predicate, target):
            try:
                split_ref(source)
                split_ref(target)
            except ContextError:
                return
            edges.append({"source_ref": source, "predicate": predicate, "target_ref": target, "origin": "legacy_projection", "status": "recorded", "evidence_refs": []})
        kind, identifier = split_ref(ref)
        for row in shared.execute("SELECT id,run_id FROM run_alerts WHERE run_id=? OR id=?", (identifier if kind == "run" else "", identifier if kind == "anomaly" else "")):
            add("run:" + row["run_id"], "has_anomaly", "anomaly:" + row["id"])
        for row in shared.execute("SELECT finding_id,run_id FROM finding_runs WHERE run_id=? OR finding_id=?", (identifier if kind == "run" else "", identifier if kind == "finding" else "")):
            add("run:" + row["run_id"], "has_finding", "finding:" + row["finding_id"])
        for row in shared.execute("SELECT * FROM finding_links WHERE from_finding_id=? OR to_finding_id=?", (identifier if kind == "finding" else "", identifier if kind == "finding" else "")):
            add("finding:" + row["from_finding_id"], row["relation"], "finding:" + row["to_finding_id"])
        # Legacy JSON records have no reverse-reference index. Restrict fallback
        # scans to the authorized project and a literal identifier match; new
        # records use the indexed overlay below.
        for row in private.execute("SELECT id,details FROM private_episodes WHERE project_id=? AND (id=? OR instr(details,?)>0)", (project_id, identifier if kind == "investigation" else "", identifier)):
            details = json_load(row["details"])
            source = "investigation:" + row["id"]
            # private_episodes.run_id is a different private-run namespace.
            # creao_run_id is also deliberately excluded.
            for key in ("source_run_ref", "simulation_run_ref"):
                if isinstance(details.get(key), str):
                    add(source, "investigates", details[key])
            if isinstance(details.get("source_run_id"), str):
                add(source, "investigates", "run:" + details["source_run_id"])
            for key in ("source_alert_ref", "anomaly_ref"):
                if isinstance(details.get(key), str):
                    add(source, "investigates", details[key])
            if isinstance(details.get("alert_id"), str):
                add(source, "investigates", "anomaly:" + details["alert_id"])
            for ref in details.get("evidence_refs", []) if isinstance(details.get("evidence_refs"), list) else []:
                add(source, "cites", ref)
            for fid in details.get("context_finding_ids", []) if isinstance(details.get("context_finding_ids"), list) else []:
                if isinstance(fid, str):
                    add(source, "cites", fid if fid.startswith("finding:") else "finding:" + fid)
            for graph in details.get("diagnostic_graphs", []) if isinstance(details.get("diagnostic_graphs"), list) else []:
                if isinstance(graph, dict) and isinstance(graph.get("id"), str):
                    add(source, "has_graph", "graph:" + graph["id"])
        for row in private.execute("SELECT id,episode_id,finding_id FROM share_drafts WHERE project_id=? AND (id=? OR episode_id=? OR finding_id=?)", (project_id, identifier if kind == "draft" else "", identifier if kind == "investigation" else "", identifier if kind == "finding" else "")):
            if row["episode_id"]:
                add("draft:" + row["id"], "derived_from", "investigation:" + row["episode_id"])
            if row["finding_id"]:
                add("draft:" + row["id"], "published_as", "finding:" + row["finding_id"])
        for row in private.execute("SELECT id,spec FROM visualizations WHERE project_id=? AND (id=? OR instr(spec,?)>0)", (project_id, identifier if kind == "graph" else "", ref)):
            spec = json_load(row["spec"])
            for layer in spec.get("layers", []) if isinstance(spec.get("layers"), list) else []:
                if isinstance(layer, dict):
                    ref = layer.get("dataset") or layer.get("dataset_id")
                    if ref:
                        add("graph:" + row["id"], "visualizes", ref)
            for dataset in spec.get("datasets", []) if isinstance(spec.get("datasets"), list) else []:
                ref = dataset.get("id") if isinstance(dataset, dict) else dataset
                if ref:
                    add("graph:" + row["id"], "visualizes", ref)
        if kind in {"worksheet", "finding"}:
            for row in shared.execute("""SELECT wi.worksheet_id,wi.finding_ids FROM worksheet_items wi
                    JOIN worksheets w ON w.id=wi.worksheet_id WHERE w.project_id=? AND
                    (wi.worksheet_id=? OR instr(wi.finding_ids,?)>0)""", (project_id, identifier if kind == "worksheet" else "", identifier if kind == "finding" else "\0")):
                for finding_id in json_load(row["finding_ids"], []):
                    if isinstance(finding_id, str):
                        add("worksheet:" + row["worksheet_id"], "teaches", "finding:" + finding_id)
        return edges

    def get_run_context(self, run_id, limit=30, cursor=None):
        ref = run_id if isinstance(run_id, str) and run_id.startswith("run:") else "run:" + str(run_id)
        return self.get_context(ref, depth=5, limit=limit, cursor=cursor)

    def get_context(self, ref, depth=2, limit=30, cursor=None):
        split_ref(ref)
        if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 5:
            raise ContextError("Depth must be between zero and five.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECORDS:
            raise ContextError("Limit must be between one and sixty.")
        # Authorize before asking even the caller's connector about a project.
        with closing(self.db.shared()) as shared, closing(self.db.private(self.member["id"])) as private:
            self._active(shared)
            if self.project_id:
                self._project(shared, self.project_id)
            try:
                root = self._resolve(ref, shared, private)
                project_id = root["project_id"]
            except ContextNotFound:
                if not self.project_id or split_ref(ref)[0] not in _LOCAL_KINDS:
                    raise
                project_id = self.project_id
                root = None
        self._refresh(project_id, ref)
        with closing(self.db.shared()) as shared, closing(self.db.private(self.member["id"])) as private:
            root = self._resolve(ref, shared, private)
            resolved = {ref: root}
            inaccessible = set()
            def resolve(candidate):
                if candidate in inaccessible:
                    return None
                if candidate not in resolved:
                    try:
                        resolved[candidate] = self._resolve(candidate, shared, private)
                    except ContextNotFound:
                        inaccessible.add(candidate)
                        return None
                return resolved[candidate]
            missing = object()
            def safe_references(value, key=None):
                if isinstance(value, str):
                    candidate = value.partition("|")[0]
                    try:
                        split_ref(candidate)
                    except ContextError:
                        return value
                    return value if resolve(candidate) else missing
                if isinstance(value, list):
                    result = []
                    for item in value:
                        if key == "context_finding_ids" and isinstance(item, str):
                            if not resolve(item if item.startswith("finding:") else "finding:" + item):
                                continue
                        child = safe_references(item)
                        if child is not missing:
                            result.append(child)
                    return result
                if isinstance(value, dict):
                    result = {}
                    for field, item in value.items():
                        if safe_references(field) is missing:
                            continue
                        child = safe_references(item, field)
                        if child is not missing:
                            result[field] = child
                    return result
                return value
            ordered = [ref]
            selected_edges = []
            frontier = [ref]
            for _ in range(depth):
                following = []
                for current in frontier:
                    edges = self._legacy_links(shared, private, current, resolved[current]["project_id"])
                    for conn in (shared, private):
                        edges.extend(_link_row(r) for r in conn.execute(
                            "SELECT * FROM context_links WHERE source_ref=? OR target_ref=? ORDER BY created_at,id", (current, current)))
                    for edge in edges:
                        if edge["source_ref"] != current and edge["target_ref"] != current:
                            continue
                        if not resolve(edge["source_ref"]) or not resolve(edge["target_ref"]):
                            continue
                        # Evidence IDs themselves are access-controlled endpoints.
                        clean_edge = {key: value for key, value in edge.items() if key not in {"evidence_refs"}}
                        clean_edge["evidence_refs"] = [e for e in edge.get("evidence_refs", []) if resolve(e)]
                        key = (edge["source_ref"], edge["predicate"], edge["target_ref"])
                        if not any((e["source_ref"], e["predicate"], e["target_ref"]) == key for e in selected_edges):
                            selected_edges.append(clean_edge)
                        other = edge["target_ref"] if edge["source_ref"] == current else edge["source_ref"]
                        if other not in ordered:
                            ordered.append(other)
                            following.append(other)
                frontier = following
                if not frontier:
                    break
            cursor_scope = {"root": ref, "member": self.member["id"], "purpose": self.purpose, "project": self.project_id, "depth": depth}
            offset = 0
            if cursor:
                try:
                    token = json_load(base64.urlsafe_b64decode(cursor.encode()).decode())
                    if token.get("scope") != cursor_scope or not isinstance(token.get("offset"), int) or token["offset"] < 0:
                        raise ValueError()
                    offset = token["offset"]
                except Exception:
                    raise ContextError("The context cursor is invalid for this request.") from None
            records = []
            page_refs = []
            index = min(offset, len(ordered))
            while index < len(ordered) and len(records) < limit:
                record = resolved[ordered[index]]
                record = {**record, "authorized_summary": safe_references(record["authorized_summary"]), "provenance": safe_references(record["provenance"])}
                # One large record may not consume the whole context window.
                if len(canonical(record)) > 4400:
                    record = {**record, "authorized_summary": {"excerpt": safe_text(canonical(record["authorized_summary"]), 2300), "content_truncated": True}}
                trial = records + [record]
                page_set = {r["ref"] for r in trial}
                trial_edges = [e for e in selected_edges if e["source_ref"] in page_set and e["target_ref"] in page_set]
                # Reserve room for completeness/cursor/section metadata.
                if records and len(canonical({"records": trial, "relationships": trial_edges})) > MAX_CHARS - 2400:
                    break
                records.append(record)
                page_refs.append(record["ref"])
                index += 1
            page_set = set(page_refs)
            visible_edges = [e for e in selected_edges if e["source_ref"] in page_set and e["target_ref"] in page_set]
            truncated = index < len(ordered) or self._source_truncated
            next_cursor = base64.urlsafe_b64encode(canonical({"scope": cursor_scope, "offset": index}).encode()).decode() if index < len(ordered) else None
            sections = {}
            for name, kinds in _SECTIONS.items():
                refs = [r["ref"] for r in records if r["kind"] in kinds]
                sections[name] = {"availability": "captured" if refs else "not_captured_or_not_available", "refs": refs}
            answer = {"root": ref, "purpose": self.purpose, "records": records, "relationships": visible_edges,
                "sections": sections, "completeness": {"connector": self._connector_state,
                    "legacy_metadata": "unknown_unless_captured", "publication_scope": "eligible_material_only" if self.purpose == "project_publication" else "private_analysis",
                    "page": "partial" if truncated or cursor else "complete_with_explicit_gaps"},
                "truncated": truncated, "next_cursor": next_cursor}
            # The outer envelope is also budgeted, including repeated section IDs.
            while len(canonical(answer)) > MAX_CHARS and len(answer["records"]) > 1:
                removed = answer["records"].pop()["ref"]
                index -= 1
                answer["relationships"] = [e for e in answer["relationships"] if removed not in (e["source_ref"], e["target_ref"])]
                for section in answer["sections"].values():
                    section["refs"] = [r for r in section["refs"] if r != removed]
                    if not section["refs"]:
                        section["availability"] = "not_captured_or_not_available"
                answer["truncated"] = True
                answer["completeness"]["page"] = "partial"
                answer["next_cursor"] = base64.urlsafe_b64encode(canonical({"scope": cursor_scope, "offset": index}).encode()).decode()
            return answer

    def propose_relationship(self, project_id, source_ref, predicate, target_ref, evidence_refs=None, provider_run_id=None):
        if predicate not in {"associated_with", "derived_from", "supports", "contradicts", "causes", "resolves", "tested_with", "similar_to", "cites", "investigates"}:
            raise ContextError("This relationship predicate is not available for proposals.")
        with closing(self.db.shared()) as shared, closing(self.db.private(self.member["id"])) as private:
            self._project(shared, project_id)
            for ref in [source_ref, target_ref, *(evidence_refs or [])]:
                record = self._resolve(ref, shared, private)
                if record["project_id"] != project_id:
                    raise ContextError("Proposals must name records in the same project.")
            result = _link(private, project_id, source_ref, predicate, target_ref, "creao_proposal", "proposed", evidence_refs, provider_run_id)
            private.commit()
            if result["status"] != "proposed":
                result = {"project_id": project_id, "source_ref": source_ref, "predicate": predicate, "target_ref": target_ref,
                    "origin": "creao_proposal", "status": "proposed", "evidence_refs": list(evidence_refs or []),
                    "provider_run_id": provider_run_id, "already_recorded": True}
            return {"relationship": result, "scientific_state": "hypothesis", "verification_changed": False}

    def ingest_local(self, project_id, records_or_events):
        """Apply authenticated owner metadata. Receipt and projection share a transaction."""
        with closing(self.db.shared()) as shared:
            self._project(shared, project_id)
            if self.project_id and project_id != self.project_id:
                raise ContextNotFound("Research context was not found.")
        if isinstance(records_or_events, list):
            envelope = {"events": records_or_events} if records_or_events and "event_id" in records_or_events[0] else {"records": records_or_events}
        elif isinstance(records_or_events, dict):
            envelope = records_or_events
        else:
            raise ContextError("Expected records or an event envelope.")
        records, events = envelope.get("records", []), envelope.get("events", [])
        if not isinstance(records, list) or not isinstance(events, list) or len(records) > 200 or len(events) > 400:
            raise ContextError("Local context delivery is too large.")
        if len(canonical(envelope)) > 1_000_000:
            raise ContextError("Local context delivery is too large.")
        deliveries = list(events)
        covered = {hashlib.sha256(canonical(event["payload"]).encode()).hexdigest()
            for event in events if isinstance(event, dict) and isinstance(event.get("payload"), dict)}
        for record in records:
            if not isinstance(record, dict):
                raise ContextError("Malformed local context record.")
            fingerprint = hashlib.sha256(canonical(record).encode()).hexdigest()
            if fingerprint in covered:
                continue
            deliveries.append({"producer_id": "local-context-records", "event_id": fingerprint,
                "project_id": project_id, "owner_id": self.member["id"], "event_type": "record.updated",
                "subject_ref": record.get("ref"), "source_revision": record.get("source_revision", 0),
                "occurred_at": record.get("updated_at") or "unknown", "payload": record})
        counts = {"applied": 0, "replayed": 0, "stale": 0, "acknowledged_event_ids": []}
        with closing(self.db.private(self.member["id"])) as private:
            try:
                for event in deliveries:
                    if not isinstance(event, dict):
                        raise ContextError("Malformed local context event.")
                    if event.get("project_id") != project_id or event.get("owner_id") != self.member["id"]:
                        raise ContextError("Local context owner or project does not match the authenticated session.")
                    producer, event_id = event.get("producer_id"), event.get("event_id")
                    if not all(isinstance(v, str) and 0 < len(v) <= 240 for v in (producer, event_id)):
                        raise ContextError("Local context needs bounded producer and event IDs.")
                    record = event.get("payload")
                    if not isinstance(record, dict) or record.get("ref") != event.get("subject_ref"):
                        raise ContextError("An event must contain its matching record.")
                    kind, _ = split_ref(record["ref"])
                    if kind not in _LOCAL_KINDS or record.get("kind") != _LOCAL_KINDS[kind]:
                        raise ContextError("Only local simulation, experiment and baseline records can be ingested.")
                    if record.get("project_id") != project_id or record.get("owner_id") != self.member["id"]:
                        raise ContextError("Local record owner or project does not match the authenticated session.")
                    revision = event.get("source_revision", 0)
                    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0 or revision != record.get("source_revision", 0):
                        raise ContextError("A source revision must be a matching nonnegative integer.")
                    payload_hash = hashlib.sha256(canonical(event).encode()).hexdigest()
                    prior = private.execute("SELECT payload_hash,state FROM context_events WHERE producer_id=? AND event_id=?", (producer, event_id)).fetchone()
                    if prior:
                        if prior["payload_hash"] != payload_hash:
                            raise ContextConflict("An event ID was reused with different content.")
                        counts["replayed"] += 1
                        counts["acknowledged_event_ids"].append({"producer_id": producer, "event_id": event_id})
                        continue
                    existing = private.execute("SELECT metadata FROM context_artifacts WHERE project_id=? AND owner_id=? AND kind=? AND source_key=?", (project_id, self.member["id"], record["kind"], "record:" + record["ref"])).fetchone()
                    old = json_load(existing["metadata"]) if existing else {}
                    record_hash = hashlib.sha256(canonical(record).encode()).hexdigest()
                    old_revision = old.get("source_revision", -1)
                    if old_revision == revision and old.get("_source_hash") and old["_source_hash"] != record_hash:
                        raise ContextConflict("A source revision was reused with different metadata.")
                    stale = old_revision > revision
                    if not stale:
                        self._apply_local(private, project_id, record, revision, record_hash)
                        counts["applied"] += 1
                    else:
                        counts["stale"] += 1
                    stamp = now()
                    # Store a minimal receipt, not the complete event/source data twice.
                    receipt = {"ref": record["ref"], "source_revision": revision, "stale": stale}
                    private.execute("""INSERT INTO context_events(producer_id,event_id,project_id,owner_id,event_type,subject_ref,
                        source_revision,payload_hash,payload,state,occurred_at,applied_at) VALUES(?,?,?,?,?,?,?,?,?,'applied',?,?)""",
                        (producer, event_id, project_id, self.member["id"], safe_text(str(event.get("event_type", "record.updated")), 80), record["ref"], revision,
                         payload_hash, canonical(receipt), str(event.get("occurred_at") or stamp)[:80], stamp))
                    counts["acknowledged_event_ids"].append({"producer_id": producer, "event_id": event_id})
                private.commit()
            except Exception:
                private.rollback()
                raise
        return counts

    def _apply_local(self, private, project_id, record, revision, record_hash):
        metadata = sanitize(record.get("metadata", {}))
        if not isinstance(metadata, dict):
            raise ContextError("Local record metadata must be an object.")
        metadata.update({"canonical_ref": record["ref"], "source_revision": revision, "updated_at": record.get("updated_at"), "_source_hash": record_hash})
        _artifact(private, self.member["id"], project_id, record["kind"], "record:" + record["ref"], metadata)
        for artifact in record.get("artifacts", [])[:20]:
            if not isinstance(artifact, dict):
                raise ContextError("Malformed local artifact descriptor.")
            saved = _artifact(private, self.member["id"], project_id, artifact.get("kind"), artifact.get("source_key"),
                {**(artifact.get("metadata") or {}), "digest": artifact.get("digest")}, artifact.get("locator"), artifact.get("digest"))
            predicate = {"execution_snapshot": "uses_snapshot", "code_version": "uses_code", "input_manifest": "uses_inputs", "output_artifact": "produced_result"}.get(artifact.get("kind"), "cites")
            _link(private, project_id, record["ref"], predicate, saved["ref"], "local_capture")
        for key, predicate, reverse in (("backend_run_ref", "executed_as", False), ("origin_run_ref", "derived_from", False),
                ("origin_anomaly_ref", "investigates", False), ("origin_investigation_ref", "tested_with", True),
                ("parent_ref", "derived_from", False), ("baseline_ref", "compared_with", False)):
            target = metadata.get(key)
            if isinstance(target, str):
                split_ref(target)
                _link(private, project_id, target if reverse else record["ref"], predicate, record["ref"] if reverse else target, "local_capture")
        baseline_ref = metadata.get("baseline_ref")
        if record["kind"] == "experiment" and isinstance(baseline_ref, str) and split_ref(baseline_ref)[0] == "base":
            # Existing graph baseline IDs are aliases of the set's declared
            # original values, not a guessed historic execution or Git revision.
            baseline_metadata = {"canonical_ref": baseline_ref, "parameters": metadata.get("baseline_parameters", {}),
                "parameter_completeness": metadata.get("baseline_parameter_completeness", "partial"),
                "status": "recorded_baseline", "source_revision": revision, "updated_at": record.get("updated_at"),
                "set_id": metadata.get("set_id"), "baseline_kind": "declared_original_values"}
            _artifact(private, self.member["id"], project_id, "baseline", "record:" + baseline_ref, baseline_metadata)

    def find_related_experiments(self, project_id, parameters, tolerance=0, limit=10):
        if not isinstance(parameters, dict) or not parameters or len(parameters) > 30:
            raise ContextError("Provide explicit parameter values to compare.")
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
            raise ContextError("Tolerance must be a finite nonnegative absolute tolerance.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
            raise ContextError("Limit must be between one and thirty.")
        with closing(self.db.shared()) as shared:
            self._project(shared, project_id)
        self._refresh(project_id)
        matches = []
        with closing(self.db.shared()) as shared, closing(self.db.private(self.member["id"])) as private:
            self._project(shared, project_id)
            for row in private.execute("SELECT metadata FROM context_artifacts WHERE project_id=? AND owner_id=? AND kind='experiment' ORDER BY updated_at DESC,id", (project_id, self.member["id"])):
                metadata = json_load(row["metadata"])
                actual = metadata.get("parameters", {})
                if not isinstance(actual, dict):
                    continue
                def same(key, wanted):
                    if key not in actual:
                        return False
                    value = actual[key]
                    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) and isinstance(value, (int, float)) and not isinstance(value, bool):
                        return math.isfinite(wanted) and math.isfinite(value) and abs(value - wanted) <= tolerance
                    return type(value) is type(wanted) and value == wanted
                if all(same(key, wanted) for key, wanted in parameters.items()):
                    try:
                        matches.append(self._resolve(metadata["canonical_ref"], shared, private))
                    except ContextNotFound:
                        continue
                    if len(matches) > limit:
                        break
        answer = {"project_id": project_id, "criteria": sanitize(parameters), "absolute_tolerance": tolerance,
            "matches": matches[:limit], "truncated": len(matches) > limit,
            "completeness": {"connector": self._connector_state, "parameter_values": "explicit_captured_values_only", "missing_parameters": "never_inferred_from_labels"}}
        while len(canonical(answer)) > MAX_CHARS and answer["matches"]:
            answer["matches"].pop()
            answer["truncated"] = True
        return answer
