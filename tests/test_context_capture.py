"""Provenance is immutable, private, replayable and honest about missing capture."""
import hashlib
import copy
import json
from pathlib import Path

import pytest

from local_connector import context
from local_connector.context import (
    MAX_INDEX_FILES, MAX_PENDING_EVENTS, acknowledge_events, capture_snapshot,
    declared_parameters, export_records, git_head, read_artifact,
)
from local_connector.experiments.manager import ExperimentManager
from local_connector.experiments.sandbox import Outcome
from local_connector.file_access.grants import AccessDenied, GrantRegistry
from local_connector.simulations import SimulationManager


def file_hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.py").write_text("print('step 1 energy = 2')\n")
    (root / "config.json").write_text(json.dumps({"dt": .03, "steps": 100, "api_key": "private-config-value",
                                                "not_a_parameter": "private-unlisted-value"}))
    (root / "baseline.log").write_text("step 1 energy = 4\n")
    (root / "expandrg.json").write_text(json.dumps({"entry_points": [{"name": "heat",
        "command": ["{python}", "main.py"], "config": "config.json", "parameters": ["dt", "steps"]}]}))
    (root / ".env").write_text("KEY=capi_never_capture_this_secret\n")
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
    (git / "config").write_text("[core]\nfsmonitor = dangerous-hook\n[filter \"x\"]\nclean = dangerous-filter\n")
    return root


@pytest.fixture
def local(project, tmp_path):
    registry = GrantRegistry(tmp_path / "grants.json")
    grant = registry.grant(project)
    sim = SimulationManager(tmp_path / "simulations", find_program=lambda *args, **kwargs: None)
    exp = ExperimentManager(tmp_path / "experiments")
    return sim, exp, registry, grant


def prepare_sim(local, **kwargs):
    sim, _, _, grant = local
    return sim.prepare(grant, "alice", "heat", runtime={"source_root": str(grant.root)}, **kwargs)


def prepare_exp(local, project, changes=None, **kwargs):
    return local[1].prepare(project, "Heat", "alice", "heat", "heat", changes or {}, **kwargs)


def test_saved_versions_keep_exact_hashes_and_stable_repeat_parent(local, project):
    sim, exp, registry, _ = local
    before = file_hashes(project)
    saved = prepare_sim(local)
    captured = saved["research_context"]["capture"]
    assert saved["fingerprint"] == captured["snapshot_fingerprint"]
    assert captured["git_commit"] == "a" * 40 and captured["git_dirty"] is None
    assert file_hashes(project) == before
    old_text = (project / "main.py").read_text()
    (project / "main.py").write_text("print('changed source')\n")
    (project / "config.json").write_text('{"dt":99,"steps":2}')
    (project / ".git" / "refs" / "heads" / "main").write_text("b" * 40)
    repeated = sim.repeat(saved["id"])
    assert repeated["id"] != saved["id"]
    assert repeated["parent_simulation_id"] == saved["id"]
    assert repeated["source_version"] == saved["version"]
    assert repeated["fingerprint"] == saved["fingerprint"]
    assert repeated["research_context"]["capture"]["git_commit"] == "a" * 40
    evidence = read_artifact(sim, exp, "alice", "heat", f"sim:{saved['id']}", "code", "main.py", registry)
    assert evidence["text"].replace("\r\n", "\n") == old_text
    fresh = prepare_sim(local)
    assert fresh["fingerprint"] != saved["fingerprint"]


def test_code_and_input_identities_are_distinct(local, project):
    first = prepare_sim(local)["research_context"]["capture"]
    (project / "config.json").write_text('{"dt":.01}')
    second = prepare_sim(local)["research_context"]["capture"]
    assert first["snapshot_fingerprint"] != second["snapshot_fingerprint"]
    assert first["input_fingerprint"] != second["input_fingerprint"]
    assert first["code_fingerprint"] == second["code_fingerprint"]


def test_effective_declared_parameters_and_origins_are_captured(local, project):
    exp = prepare_exp(local, project, {"dt": .02}, set_id="set-1", baseline="baseline.log",
                      origin_run_ref="run:r1", origin_anomaly_ref="anomaly:a1",
                      origin_investigation_ref="investigation:i1")
    envelope = export_records(*local[:2], "alice", "heat")
    exported = next(row for row in envelope["records"] if row["ref"] == f"exp:{exp['id']}")
    metadata = exported["metadata"]
    assert metadata["parameters"] == {"dt": .02, "steps": 100}
    assert metadata["parameter_completeness"] == "complete_declared"
    assert metadata["baseline_parameters"] == {"dt": .03, "steps": 100}
    assert metadata["baseline_ref"] == "base:set-1"
    assert metadata["baseline_artifact"]["relative_path"] == "baseline.log"
    assert metadata["origin_run_ref"] == "run:r1"
    assert metadata["origin_anomaly_ref"] == "anomaly:a1"
    assert metadata["origin_investigation_ref"] == "investigation:i1"
    body = json.dumps(envelope)
    assert str(project) not in body and str(local[0].home) not in body
    assert "private-config-value" not in body and "private-unlisted-value" not in body
    assert "capi_never_capture_this_secret" not in body and '"root"' not in body
    assert '"runtime"' not in body and '".git/' not in body and '".env"' not in body


def test_parameter_capture_never_exports_secret_or_absolute_values(project):
    manifest = json.loads((project / "expandrg.json").read_text())
    manifest["entry_points"][0]["parameters"] += ["api_key", "output", "missing"]
    (project / "expandrg.json").write_text(json.dumps(manifest))
    config = json.loads((project / "config.json").read_text())
    config["output"] = "C:\\Users\\alice\\private\\results"
    (project / "config.json").write_text(json.dumps(config))
    parameters, completeness = declared_parameters(project, "heat")
    assert parameters == {"dt": .03, "steps": 100, "missing": None}
    assert completeness == "partial"


def test_experiment_execution_cannot_change_its_immutable_source(local, project):
    sim, exp, registry, _ = local
    original = file_hashes(project)
    record = prepare_exp(local, project, {"dt": .02})
    retained = exp.home / record["id"] / "project"
    before = file_hashes(retained)
    def runner(command, cwd, log, limits, cancel):
        assert cwd.name == "work"
        (cwd / "main.py").write_text("solver rewrote its source")
        (cwd / "config.json").write_text('{"dt":500}')
        log.write_text("step 1 energy = 2\n")
        return Outcome("finished", "", 0, .01)
    exp._run = runner
    exp.run([record["id"]])
    exp.wait(5)
    assert file_hashes(project) == original and file_hashes(retained) == before
    exported = export_records(sim, exp, "alice", "heat")["records"][0]
    assert exported["metadata"]["parameters"]["dt"] == .02
    output = next(item for item in exported["artifacts"] if item["kind"] == "output_artifact")
    assert output["digest"] == hashlib.sha256((exp.home / record["id"] / "output.log").read_bytes()).hexdigest()
    evidence = read_artifact(sim, exp, "alice", "heat", exported["ref"], "output", "output.log", registry)
    assert evidence["text"].replace("\r\n", "\n") == "step 1 energy = 2\n"


def test_replayed_exports_and_acknowledgements_survive_restart(local):
    sim, exp, _, _ = local
    record = prepare_sim(local, origin_run_ref="r1")
    first = export_records(sim, exp, "alice", "heat")
    sim._save(sim.records[record["id"]])
    assert export_records(sim, exp, "alice", "heat") == first
    restarted = SimulationManager(sim.home)
    assert export_records(restarted, exp, "alice", "heat") == first
    event_ids = [event["event_id"] for event in first["events"]]
    assert acknowledge_events(restarted, exp, "bob", "heat", event_ids) == {"acknowledged": []}
    assert acknowledge_events(restarted, exp, "alice", "other", event_ids) == {"acknowledged": []}
    assert acknowledge_events(restarted, exp, "alice", "heat", event_ids) == {"acknowledged": event_ids}
    restarted = SimulationManager(sim.home)
    after = export_records(restarted, exp, "alice", "heat")
    assert after["records"] == first["records"] and after["events"] == []
    assert acknowledge_events(restarted, exp, "alice", "heat", event_ids) == {"acknowledged": []}
    assert export_records(restarted, exp, "bob", "heat") == {"records": [], "events": [], "truncated": False}


def test_terminal_output_is_hashed_once_and_state_outbox_is_bounded(local, monkeypatch):
    sim, exp, _, _ = local
    saved = prepare_sim(local)
    record = sim.records[saved["id"]]
    calls = []
    digest = context._digest
    def tracking(path):
        calls.append(path)
        return digest(path)
    monkeypatch.setattr(context, "_digest", tracking)
    for index in range(MAX_PENDING_EVENTS + 5):
        record.update(status="running", seconds=index)
        sim._save(record)
    assert calls == []  # no project rehashing while work/log updates are arriving
    output = sim.home / record["id"] / "output.log"
    output.write_text("finished\n")
    record.update(status="finished", finished_at="2026-10-25T00:00:00Z")
    sim._save(record)
    revision = record["research_context"]["revision"]
    sim._save(record)
    assert calls == [output]
    assert record["research_context"]["revision"] == revision
    envelope = export_records(sim, exp, "alice", "heat")
    assert len(record["research_context"]["pending_events"]) == MAX_PENDING_EVENTS
    assert len(envelope["events"]) == 1  # bounded delivery includes the latest complete projection
    assert envelope["events"][-1]["source_revision"] == revision
    assert envelope["records"][0]["source_revision"] == revision


def test_artifact_owner_scope_and_live_grant_revocation(local):
    sim, exp, registry, grant = local
    saved = prepare_sim(local)
    ref = f"sim:{saved['id']}"
    with pytest.raises(AccessDenied):
        read_artifact(sim, exp, "bob", "heat", ref, "code", "main.py", registry)
    with pytest.raises(AccessDenied):
        read_artifact(sim, exp, "alice", "other", ref, "code", "main.py", registry)
    registry.revoke(grant.root)
    with pytest.raises(AccessDenied):
        read_artifact(sim, exp, "alice", "heat", ref, "code", "main.py", registry)


def test_changed_and_unindexed_artifacts_are_unavailable(local):
    sim, exp, registry, _ = local
    saved = prepare_sim(local)
    ref = f"sim:{saved['id']}"
    (sim.home / saved["id"] / "snapshot" / "main.py").write_text("changed retained artifact")
    with pytest.raises(AccessDenied, match="changed"):
        read_artifact(sim, exp, "alice", "heat", ref, "code", "main.py", registry)
    with pytest.raises(AccessDenied):
        read_artifact(sim, exp, "alice", "heat", ref, "snapshot", "../simulation.json", registry)
    with pytest.raises(AccessDenied):
        read_artifact(sim, exp, "alice", "heat", ref, "snapshot", ".env", registry)


def test_bounded_file_index_does_not_weaken_execution_digest(tmp_path):
    for index in range(MAX_INDEX_FILES + 2):
        (tmp_path / f"input-{index:03}.txt").write_text(str(index))
    first = capture_snapshot(tmp_path)
    assert len(first["file_index"]) == MAX_INDEX_FILES and first["file_index_truncated"]
    (tmp_path / f"input-{MAX_INDEX_FILES + 1:03}.txt").write_text("changed beyond the index")
    second = capture_snapshot(tmp_path)
    assert first["file_index"] == second["file_index"]
    assert first["snapshot_fingerprint"] != second["snapshot_fingerprint"]
    assert first["input_fingerprint"] != second["input_fingerprint"]


def test_legacy_records_do_not_backfill_current_revisions(local):
    sim, exp, _, _ = local
    saved = prepare_sim(local)
    sim.records[saved["id"]].pop("research_context")
    exported = export_records(sim, exp, "alice", "heat")["records"][0]
    assert exported["metadata"]["snapshot_fingerprint"] is None
    assert exported["metadata"]["parameter_completeness"] == "not_captured"
    assert exported["artifacts"] == [] and exported["source_revision"] == 0


def test_git_head_is_a_bounded_metadata_read_without_running_git(project, monkeypatch):
    import subprocess
    def refuse(*args, **kwargs):
        pytest.fail("Git capture must not execute project hooks, filters or subprocesses")
    monkeypatch.setattr(subprocess, "run", refuse)
    assert git_head(project) == "a" * 40
    (project / ".git" / "refs" / "heads" / "main").unlink()
    (project / ".git" / "packed-refs").write_text("# pack-refs\n" + "b" * 40 + " refs/heads/main\n")
    assert git_head(project) == "b" * 40
    (project / ".git" / "HEAD").write_text("ref: refs/../../outside\n")
    assert git_head(project) is None


def test_project_without_an_unambiguous_manifest_has_no_invented_parameters(project):
    (project / "expandrg.json").unlink()
    assert declared_parameters(project) == ({}, "not_captured")


def test_explicit_lookup_reaches_old_versions_beyond_recent_export_limit(local):
    sim, exp, _, _ = local
    saved = prepare_sim(local)
    for number in range(60):
        legacy = copy.deepcopy(saved)
        legacy.update(id=f"old-{number}", created_at=f"2020-01-{number + 1:02d}", research_context={})
        sim.records[legacy["id"]] = legacy
    wanted = "sim:old-0"
    assert wanted not in {r["ref"] for r in export_records(sim, exp, "alice", "heat", limit=1)["records"]}
    selected = export_records(sim, exp, "alice", "heat", limit=1, record_ref=wanted)
    assert [r["ref"] for r in selected["records"]] == [wanted]
    assert selected["records"][0]["metadata"]["snapshot_fingerprint"] is None
    assert export_records(sim, exp, "bob", "heat", record_ref=wanted)["records"] == []
