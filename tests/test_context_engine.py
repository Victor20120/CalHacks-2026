"""Context is deterministic, bounded, replay-safe and never widens source access.

No CREAO client is constructed and every connector in these tests is in-memory.
"""
import copy
import json
from contextlib import closing

import pytest

from backend.context_engine import (
    ContextConflict, ContextEngine, ContextError, ContextNotFound,
    link, register_investigation, store_artifact,
)
from backend.db.database import Database
from backend.db.schema import SCHEMA_VERSION
from backend.workspaces.demo import seed_demo

PROJECT = "heat-diffusion"
WHEN = "2026-10-25T12:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "data", seed_demo)
    with closing(database.shared()) as conn:
        for identifier, owner, project in (("r1", "researcher_a", PROJECT), ("r2", "researcher_b", PROJECT), ("restricted", "researcher_a", "wave-propagation")):
            conn.execute("INSERT INTO simulation_runs VALUES(?,?,?,?,?,?,?,?,?)", (identifier, "demo-lab", project, owner, identifier, 0, 3, WHEN, WHEN))
        conn.executemany("INSERT INTO run_points VALUES(?,?,?,?)", [("r1", "residual", 1, 1.0), ("r1", "residual", 2, 0.1), ("r1", "residual", 3, None)])
        conn.execute("INSERT INTO run_alerts VALUES('a1','r1','critical','nan_inf','Non-finite residual','residual',3,7,'residual=nan',?)", (WHEN,))
        conn.execute("INSERT INTO approvals VALUES('approval','demo-lab',?,'researcher_a','d1',0,?)", (PROJECT, WHEN))
        conn.execute("INSERT INTO shared_findings VALUES('f1','demo-lab',?,'error_diagnosis','Possible dt problem','Try a smaller dt','hypothesis','researcher_a','approval','provider-1',?)", (PROJECT, WHEN))
        conn.execute("INSERT INTO finding_runs VALUES('f1','r1',?)", (WHEN,))
        conn.execute("INSERT INTO finding_notes VALUES('n1','f1','researcher_a','detail','Agent-readable note',?)", (WHEN,))
        conn.execute("INSERT INTO finding_comments VALUES('c1','f1','leader','HUMAN_ONLY_MARKER',?)", (WHEN,))
        conn.commit()
    return database


def member(db, identifier="researcher_a"):
    with closing(db.shared()) as conn:
        return dict(conn.execute("SELECT * FROM members WHERE id=?", (identifier,)).fetchone())


def engine(db, identifier="researcher_a", **kwargs):
    return ContextEngine(db, member(db, identifier), **kwargs)


def episode(db, identifier="investigation-1", details=None, project=PROJECT):
    with closing(db.private("researcher_a")) as conn:
        conn.execute("INSERT INTO private_episodes VALUES(?,?,'diagnosis','Possible time step instability',?,'hypothesis',NULL,'CREAO_NOT_SIMULATION',?)", (identifier, project, json.dumps(details or {}), WHEN))
        conn.commit()


def local_record(ref="sim:s1", revision=1):
    return {"ref": ref, "kind": "experiment" if ref.startswith("exp:") else "simulation", "project_id": PROJECT,
        "owner_id": "researcher_a", "source_revision": revision, "updated_at": WHEN,
        "metadata": {"backend_run_ref": "run:r1", "parameters": {"dt": 0.02}, "parameter_completeness": "partial",
            "snapshot_fingerprint": "f" * 64, "git_commit": None, "git_dirty": None, "status": "finished"},
        "artifacts": [{"kind": "execution_snapshot", "source_key": "snapshot:" + "f" * 64,
            "digest": "f" * 64, "metadata": {"retained": True}, "locator": {"record_ref": ref, "role": "execution_snapshot"}}]}


def local_event(record, identifier="event-1"):
    return {"producer_id": "local-test", "event_id": identifier, "schema_version": 1, "event_type": record["kind"] + ".updated",
        "subject_ref": record["ref"], "source_revision": record["source_revision"], "project_id": PROJECT,
        "owner_id": "researcher_a", "occurred_at": WHEN, "payload": record}


def test_additive_tables_exist_in_shared_and_private_without_schema_bump(db):
    assert SCHEMA_VERSION == 2
    for conn in (db.shared(), db.private("researcher_a")):
        with closing(conn):
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"context_events", "context_artifacts", "context_links"} <= names
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_legacy_run_context_reads_saved_facts_and_explicit_unknowns(db):
    answer = engine(db).get_run_context("r1")
    assert {r["ref"] for r in answer["records"]} == {"run:r1", "anomaly:a1", "finding:f1"}
    run = answer["records"][0]
    assert run["completeness"]["git_commit"] == "unknown"
    assert run["completeness"]["parameters"] == "not_captured"
    assert run["authorized_summary"]["metrics"]["residual"]["finite"] == 2
    assert answer["completeness"]["connector"] == "offline"
    text = json.dumps(answer)
    assert "Agent-readable note" in text
    assert "HUMAN_ONLY_MARKER" not in text
    assert answer["records"][-1]["scientific_state"] == "hypothesis"


def test_first_workflow_is_durable_and_reversible_from_each_source(db):
    evidence = store_artifact(db, "researcher_a", PROJECT, "evidence", "diagnosis-1:evidence-1",
        {"path": "src/solver.f90", "excerpt": "dt = 0.02", "digest": "1" * 64, "capture_authorization": "selected_for_diagnosis"})
    details = {"simulation_run_ref": "run:r1", "anomaly_ref": "anomaly:a1", "evidence_refs": [evidence["ref"]], "reply": "This remains a hypothesis."}
    episode(db, details=details)
    register_investigation(db, "researcher_a", PROJECT, "investigation-1", "r1", "a1", [evidence["ref"]], "CREAO_NOT_SIMULATION")
    register_investigation(db, "researcher_a", PROJECT, "investigation-1", "r1", "a1", [evidence["ref"]], "CREAO_NOT_SIMULATION")
    link(db, "researcher_a", PROJECT, "investigation:investigation-1", "produced_finding", "finding:f1")
    answer = engine(db).get_run_context("r1")
    refs = {r["ref"] for r in answer["records"]}
    assert {"run:r1", "anomaly:a1", "investigation:investigation-1", evidence["ref"], "finding:f1"} <= refs
    assert "run:CREAO_NOT_SIMULATION" not in json.dumps(answer)
    investigation = next(r for r in answer["records"] if r["kind"] == "investigation")
    assert investigation["scientific_state"] == "hypothesis"
    reverse = engine(db).get_context(evidence["ref"], depth=3)
    assert "run:r1" in {r["ref"] for r in reverse["records"]}
    with closing(db.private("researcher_a")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_links WHERE source_ref='investigation:investigation-1'").fetchone()[0] == 4
    with closing(db.shared()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_artifacts").fetchone()[0] == 0


def test_episode_details_reconcile_context_after_interrupted_link_write(db):
    evidence = store_artifact(db, "researcher_a", PROJECT, "evidence", "immutable", {"digest": "b" * 64, "excerpt": "retained text"})
    episode(db, details={"simulation_run_ref": "run:r1", "anomaly_ref": "anomaly:a1", "evidence_refs": [evidence["ref"]]})
    answer = engine(db).get_run_context("r1")
    assert {"investigation:investigation-1", evidence["ref"]} <= {r["ref"] for r in answer["records"]}
    assert any(e["origin"] == "legacy_projection" for e in answer["relationships"])


def test_active_membership_and_owner_checks_are_live(db):
    existing = engine(db)
    with pytest.raises(ContextNotFound):
        engine(db, "researcher_b").get_run_context("r1")
    assert engine(db, "leader").get_run_context("r1")["root"] == "run:r1"
    with closing(db.shared()) as conn:
        conn.execute("DELETE FROM project_members WHERE member_id='researcher_a' AND project_id=?", (PROJECT,))
        conn.commit()
    with pytest.raises(ContextNotFound):
        existing.get_run_context("r1")
    with closing(db.shared()) as conn:
        conn.execute("UPDATE members SET status='offboarded' WHERE id='leader'")
        conn.commit()
    with pytest.raises(ContextNotFound):
        engine(db, "leader").get_run_context("r1")


def test_shared_finding_and_leader_do_not_grant_private_context(db):
    episode(db, details={"simulation_run_ref": "run:r1", "reply": "PRIVATE_DETAILS_MARKER"})
    link(db, "researcher_a", PROJECT, "investigation:investigation-1", "supports", "finding:f1")
    for identifier in ("researcher_b", "leader"):
        answer = engine(db, identifier).get_context("finding:f1", depth=5)
        text = json.dumps(answer)
        assert "PRIVATE_DETAILS_MARKER" not in text
        assert "investigation-1" not in text
        with pytest.raises(ContextNotFound):
            engine(db, identifier).get_context("investigation:investigation-1")
    answer = engine(db, "researcher_b").get_context("finding:f1", depth=5)
    assert "run:r1" not in json.dumps(answer)


def test_publication_omits_private_payload_and_honors_readership(db):
    episode(db, details={"simulation_run_ref": "run:r1", "reply": "PRIVATE_DETAILS_MARKER"})
    evidence = store_artifact(db, "researcher_a", PROJECT, "evidence", "private", {"excerpt": "PRIVATE_EVIDENCE_MARKER"})
    register_investigation(db, "researcher_a", PROJECT, "investigation-1", "r1", "a1", [evidence["ref"]], None)
    answer = engine(db, purpose="project_publication", project_id=PROJECT).get_run_context("r1")
    assert {r["kind"] for r in answer["records"]} == {"run", "anomaly", "finding"}
    assert "PRIVATE_" not in json.dumps(answer)
    with pytest.raises(ContextNotFound):
        engine(db, purpose="project_publication", project_id=PROJECT).get_context("run:restricted")


def test_alias_capture_idempotency_stale_revisions_and_baseline(db):
    current = engine(db, project_id=PROJECT)
    record = local_record("exp:e1", 2)
    record["metadata"].update({"set_id": "set1", "baseline_ref": "base:set1", "baseline_parameters": {"dt": 0.03}})
    event = local_event(record)
    first = current.ingest_local(PROJECT, {"records": [record], "events": [event]})
    assert first["applied"] == 1
    assert current.ingest_local(PROJECT, {"events": [event]})["replayed"] == 1
    stale = local_record("exp:e1", 1)
    stale["metadata"]["parameters"]["dt"] = 0.1
    assert current.ingest_local(PROJECT, [local_event(stale, "old")])["stale"] == 1
    assert current.get_context("exp:e1")["records"][0]["authorized_summary"]["parameters"] == {"dt": 0.02}
    assert current.get_context("base:set1")["records"][0]["authorized_summary"]["parameters"] == {"dt": 0.03}
    assert "exp:e1" in {r["ref"] for r in current.get_run_context("r1")["records"]}
    with closing(db.private("researcher_a")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM context_artifacts WHERE kind='execution_snapshot'").fetchone()[0] == 1


def test_event_reuse_and_same_revision_content_changes_are_conflicts(db):
    current = engine(db)
    event = local_event(local_record())
    current.ingest_local(PROJECT, [event])
    changed = copy.deepcopy(event)
    changed["payload"]["metadata"]["parameters"]["dt"] = 0.04
    with pytest.raises(ContextConflict, match="event ID"):
        current.ingest_local(PROJECT, [changed])
    changed["event_id"] = "other-id"
    with pytest.raises(ContextConflict, match="source revision"):
        current.ingest_local(PROJECT, [changed])


def test_foreign_local_records_are_rejected_atomically(db):
    good = local_event(local_record(), "good")
    foreign = local_event(local_record("exp:foreign"), "foreign")
    foreign["payload"]["owner_id"] = "researcher_b"
    with pytest.raises(ContextError):
        engine(db).ingest_local(PROJECT, [good, foreign])
    with closing(db.private("researcher_a")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM context_artifacts").fetchone()[0] == 0


def test_connector_is_once_per_query_and_offline_retains_captured_identity(db):
    class Connector:
        calls = 0
        fail = False

        def get(self, path, params):
            self.calls += 1
            assert path == "/context/records" and params == {"project_id": PROJECT, "record_ref": "run:r1"}
            if self.fail:
                raise RuntimeError("C:/Users/private/.env capi_MUST_NOT_LEAK_1234")
            return {"records": [local_record()], "events": [], "truncated": False}
    connector = Connector()
    current = engine(db, connector=connector, project_id=PROJECT)
    answer = current.get_run_context("r1")
    assert connector.calls == 1
    assert answer["completeness"]["connector"] == "online"
    assert any(r["kind"] == "execution_snapshot" for r in answer["records"])
    connector.fail = True
    answer = current.get_run_context("r1")
    assert connector.calls == 2
    assert answer["completeness"]["connector"] == "unavailable"
    assert "MUST_NOT_LEAK" not in json.dumps(answer)
    assert any(r["kind"] == "execution_snapshot" for r in answer["records"])


def test_evidence_metadata_is_bounded_sanitized_and_immutable(db):
    artifact = store_artifact(db, "researcher_a", PROJECT, "evidence", "capture1", {
        "path": "/home/researcher/.env", "excerpt": "api_key=capi_MUST_NOT_LEAK_123456 /home/researcher/project/main.f90",
        "credentials": {"token": "MUST_NOT_LEAK"}, "digest": "d" * 64,
        "raw_contents": "MUST_NOT_LEAK", "conversation": [{"text": "MUST_NOT_LEAK"}]}, {"relative_path": "../.env"})
    text = json.dumps(engine(db).get_context(artifact["ref"]))
    assert "MUST_NOT_LEAK" not in text and "/home/researcher" not in text
    assert "requires_current_connector_grant" in text
    with pytest.raises(ContextConflict):
        store_artifact(db, "researcher_a", PROJECT, "evidence", "capture1", {"digest": "e" * 64, "excerpt": "changed"})
    with pytest.raises(ContextError):
        link(db, None, PROJECT, artifact["ref"], "supports", "finding:f1")


def test_relationship_proposals_do_not_change_verification(db):
    current = engine(db)
    proposal = current.propose_relationship(PROJECT, "anomaly:a1", "causes", "finding:f1", ["run:r1"], "provider-1")
    assert proposal["relationship"]["status"] == "proposed"
    assert proposal["verification_changed"] is False
    with closing(db.shared()) as conn:
        assert conn.execute("SELECT verification_status FROM shared_findings WHERE id='f1'").fetchone()[0] == "hypothesis"
    with pytest.raises(ContextNotFound):
        engine(db, "researcher_b").propose_relationship(PROJECT, "run:r1", "causes", "finding:f1")
    with pytest.raises(ContextError):
        current.propose_relationship(PROJECT, "run:r1", "verified", "finding:f1")


def test_graph_reverse_edges_and_hidden_dataset_references(db):
    with closing(db.private("researcher_a")) as conn:
        spec = {"datasets": ["run:r1", "run:r2"], "colors": {"run:r1": 1, "run:r2": 2}, "baseline": "run:r2"}
        conn.execute("INSERT INTO visualizations VALUES('g1',?,'Graph',?,'{}','[{""text"":""HUMAN_CHAT_MARKER""}]',?,?)", (PROJECT, json.dumps(spec), WHEN, WHEN))
        conn.commit()
    answer = engine(db).get_run_context("r1")
    assert "graph:g1" in {r["ref"] for r in answer["records"]}
    assert "run:r2" not in json.dumps(answer)
    assert "HUMAN_CHAT_MARKER" not in json.dumps(answer)


def test_parameter_search_compares_explicit_values_and_tolerance(db):
    first, second = local_record("exp:e1"), local_record("exp:e2")
    second["metadata"]["parameters"]["dt"] = 0.0201
    current = engine(db)
    current.ingest_local(PROJECT, [first, second])
    assert len(current.find_related_experiments(PROJECT, {"dt": 0.02})["matches"]) == 1
    assert len(current.find_related_experiments(PROJECT, {"dt": 0.02}, tolerance=0.001)["matches"]) == 2
    assert current.find_related_experiments(PROJECT, {"nx": 41})["matches"] == []
    with pytest.raises(ContextError):
        current.find_related_experiments(PROJECT, {"dt": 0.02}, tolerance=float("nan"))


def test_context_budget_cursors_and_deterministic_page_contents(db):
    for number in range(18):
        artifact = store_artifact(db, "researcher_a", PROJECT, "evidence", "large-" + str(number), {"excerpt": "e" * 2000})
        link(db, "researcher_a", PROJECT, "run:r1", "cites", artifact["ref"])
    current = engine(db)
    page = current.get_run_context("r1", limit=4)
    assert len(json.dumps(page, ensure_ascii=True, separators=(",", ":"))) <= 12000
    assert page["next_cursor"] and page["truncated"]
    seen = {r["ref"] for r in page["records"]}
    cursor = page["next_cursor"]
    while cursor:
        page = current.get_run_context("r1", limit=4, cursor=cursor)
        assert len(json.dumps(page, ensure_ascii=True, separators=(",", ":"))) <= 12000
        assert not seen & {r["ref"] for r in page["records"]}
        seen.update(r["ref"] for r in page["records"])
        cursor = page["next_cursor"]
    assert len(seen) == 21
    with pytest.raises(ContextError):
        current.get_context("finding:f1", cursor="not-a-cursor")
