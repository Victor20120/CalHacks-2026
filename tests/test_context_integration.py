"""Real API/storage boundaries, fake CREAO: no network, keys, or credits."""
import json
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import parse_run
from backend.db.database import Database
from backend.orchestration.toolkit import ToolError
from backend.orchestration.toolkit import ConnectorClient
from local_connector.file_access.grants import GrantRegistry
from local_connector.experiments.manager import ExperimentManager
from local_connector.simulations import SimulationManager
from local_connector.service import BackendPoster, create_service
from local_connector.simulation_monitor.checks import sample_of

PROJECT = "heat-diffusion"
FIXTURES = Path(__file__).parent / "fixtures"


class OfflineConnector:
    def get(self, *args, **kwargs):
        raise ToolError("Connector offline")

    def post(self, *args, **kwargs):
        raise ToolError("Connector offline")


class FakeCreao:
    def __init__(self):
        self.inputs = []

    def run(self, inputs):
        self.inputs.append(inputs)
        payload = json.loads((FIXTURES / "creao_hub_run_with_issue_list.json").read_text(encoding="utf-8"))
        return parse_run(payload["final_response"])


def headers(client, member="researcher_a"):
    result = client.post("/sign-in", json={"member_id": member})
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["token"]}


@pytest.fixture
def lab(tmp_path):
    fake = FakeCreao()
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda _: fake, data_dir=data, connector=OfflineConnector()))
    return client, headers(client), fake, data


def watched_run(client, auth):
    run = client.post("/simulation-runs", headers=auth,
                      json={"project_id": PROJECT, "run_label": "solver.log", "client_run_id": "source-run-1"})
    assert run.status_code == 201
    run_id = run.json()["id"]
    report = {"producer_id": "test-connector", "batch_id": "batch-1", "source_revision": 1,
              "points": [{"step": 1, "values": {"residual": 1.0}}, {"step": 2, "values": {"residual": None}}],
              "alerts": [{"severity": "critical", "check": "nan_inf", "message": "Non-finite residual",
                          "metric": "residual", "step": 2, "line_number": 2, "line": "residual=nan"}],
              "stopped": True}
    sent = client.post(f"/simulation-runs/{run_id}/report", headers=auth, json=report)
    assert sent.status_code == 200, sent.text
    detail = client.get(f"/simulation-runs/{run_id}", headers=auth).json()
    return run_id, detail["alerts"][0]["id"], report


def all_context(client, auth, run_id):
    records, relationships, cursor = {}, [], None
    for _ in range(20):
        params = {"cursor": cursor} if cursor else {}
        response = client.get(f"/context/runs/{run_id}", headers=auth, params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(response.text) < 15000
        records.update({record["ref"]: record for record in page["records"]})
        relationships.extend(page["relationships"])
        cursor = page["next_cursor"]
        if not cursor:
            return records, relationships
    pytest.fail("Context pagination did not terminate")


def test_run_anomaly_diagnosis_evidence_publication_survive_restart(lab):
    client, auth, fake, data = lab
    run_id, anomaly_id, _ = watched_run(client, auth)
    evidence = {"path": "src/solver.f90", "kind": "code", "excerpt": "18| dt = 0.03", "truncated": False}
    started = client.post("/diagnose", headers=auth, json={"project_id": PROJECT, "symptom": "Residual became non-finite",
                          "run_id": run_id, "alert_id": anomaly_id, "evidence": [evidence]})
    assert started.status_code == 202, started.text
    job = client.get("/jobs/" + started.json()["job_id"], headers=auth).json()
    assert job["status"] == "completed", job
    assert "Research context" in fake.inputs[0]["observed_symptom"]
    assert f"run:{run_id}" in fake.inputs[0]["observed_symptom"]
    assert f"anomaly:{anomaly_id}" in fake.inputs[0]["observed_symptom"]
    assert client.get(f"/context/runs/{job['diagnosis']['run_id']}", headers=auth).status_code == 404
    records, _ = all_context(client, auth, run_id)
    assert {"run", "anomaly", "investigation", "evidence", "draft"} <= {r["kind"] for r in records.values()}
    excerpt = next(r for r in records.values() if r["kind"] == "evidence")
    assert "18| dt = 0.03" in json.dumps(excerpt)
    draft_id = job["draft_id"]
    published = client.post(f"/drafts/{draft_id}/approve", headers=auth, json={})
    assert published.status_code == 200, published.text
    finding = published.json()
    assert finding["verification_status"] == "hypothesis"
    fresh = TestClient(create_app(client_factory=lambda _: fake, data_dir=data, connector=OfflineConnector()))
    fresh_auth = headers(fresh)
    records, _ = all_context(fresh, fresh_auth, run_id)
    assert "finding:" + finding["id"] in records
    investigation = next(r for r in records.values() if r["kind"] == "investigation")
    assert investigation["provenance"]["provider_run_id"] == job["diagnosis"]["run_id"]
    assert f"run:{run_id}" in json.dumps(investigation)
    # Publication grants access to the finding, never to its private investigation.
    other = headers(fresh, "researcher_b")
    view = fresh.get("/context/records/finding:" + finding["id"], headers=other).json()
    assert {r["kind"] for r in view["records"]} == {"finding"}
    assert investigation["ref"] not in json.dumps(view)


def test_ingestion_api_replays_and_conflicts_without_duplicate_observations(lab):
    client, auth, _, _ = lab
    run_id, anomaly_id, report = watched_run(client, auth)
    again = client.post("/simulation-runs", headers=auth,
                        json={"project_id": PROJECT, "run_label": "solver.log", "client_run_id": "source-run-1"})
    assert again.json()["id"] == run_id
    assert client.post(f"/simulation-runs/{run_id}/report", headers=auth, json=report).status_code == 200
    changed = {**report, "stopped": False}
    assert client.post(f"/simulation-runs/{run_id}/report", headers=auth, json=changed).status_code == 409
    detail = client.get(f"/simulation-runs/{run_id}", headers=auth).json()
    assert len(detail["series"]["residual"]) == 2
    assert [a["id"] for a in detail["alerts"]] == [anomaly_id]


def test_context_and_all_job_kinds_recheck_membership(lab):
    client, auth, _, data = lab
    run_id, _, _ = watched_run(client, auth)
    started = client.post("/diagnose", headers=auth, json={"project_id": PROJECT, "symptom": "Check residual"}).json()
    assert client.get(f"/context/runs/{run_id}", headers=headers(client, "researcher_b")).status_code == 404
    with closing(Database(data).shared()) as conn, conn:
        conn.execute("DELETE FROM project_members WHERE project_id=? AND member_id='researcher_a'", (PROJECT,))
    assert client.get(f"/context/runs/{run_id}", headers=auth).status_code == 404
    assert client.get("/jobs/" + started["job_id"], headers=auth).status_code == 404


def test_relationship_updates_are_private_proposals_and_cannot_verify_a_finding(lab):
    client, auth, _, _ = lab
    run_id, anomaly_id, _ = watched_run(client, auth)
    body = {"project_id": PROJECT, "source_ref": "anomaly:" + anomaly_id, "predicate": "causes",
            "target_ref": "run:" + run_id, "provider_run_id": "provider-not-a-simulation"}
    proposal = client.post("/context/relationships/proposals", headers=auth, json=body)
    assert proposal.status_code == 201, proposal.text
    assert proposal.json()["relationship"]["status"] == "proposed"
    assert proposal.json()["verification_changed"] is False
    assert proposal.json()["scientific_state"] == "hypothesis"
    other = headers(client, "researcher_b")
    assert client.post("/context/relationships/proposals", headers=other, json=body).status_code == 404


def test_connector_metadata_roundtrip_requires_bearer_owner_and_live_artifact_grant(tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    (source / "solver.py").write_text("dt = 0.03\n", encoding="utf-8")
    registry = GrantRegistry(tmp_path / "connector" / "grants.json")
    grant = registry.grant(source)
    simulations = SimulationManager(tmp_path / "simulations")
    experiments = ExperimentManager(tmp_path / "experiments")
    record = simulations.prepare(grant, "researcher_a", PROJECT, runtime={"source_root": str(source)})
    backend = {}

    class BackendHttp:
        def get(self, url, headers=None, timeout=None):
            return backend["client"].get("/me", headers=headers)

    local = TestClient(create_service(registry, http=BackendHttp(), simulations=simulations, experiments=experiments))

    class LocalHttp:
        def get(self, url, params=None, headers=None, timeout=None):
            return local.get(url.removeprefix("http://local"), params=params, headers=headers)

        def post(self, url, json, headers=None, timeout=None):
            return local.post(url.removeprefix("http://local"), json=json, headers=headers)

    client = TestClient(create_app(data_dir=tmp_path / "data", connector=ConnectorClient("http://local", LocalHttp())))
    backend["client"] = client
    auth = headers(client)
    other = headers(client, "researcher_b")
    assert local.get("/context/records", params={"project_id": PROJECT}).status_code == 401
    assert local.get("/context/records", params={"project_id": PROJECT}, headers=other).json()["records"] == []
    ref = "sim:" + record["id"]
    result = client.get("/context/records/" + ref, params={"project_id": PROJECT}, headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()["completeness"]["connector"] == "online"
    assert "snapshot_fingerprint" in result.text
    assert str(source) not in result.text
    assert client.get("/context/records/" + ref, params={"project_id": PROJECT}, headers=other).status_code == 404
    body = {"project_id": PROJECT, "record_ref": ref, "role": "code", "relative_path": "solver.py"}
    assert local.post("/context/artifacts/read", json=body, headers=auth).json()["text"].strip() == "dt = 0.03"
    assert local.post("/context/artifacts/read", json=body, headers=other).status_code == 404
    registry.revoke(source)
    assert local.post("/context/artifacts/read", json=body, headers=auth).status_code == 404


def test_poster_replays_frozen_reports_after_lost_ack_restart_and_new_run(lab, tmp_path):
    client, auth, _, _ = lab

    class FlakyHttp:
        lose_ack = True

        def post(self, url, json, headers=None, timeout=None):
            result = client.post(url.removeprefix("http://backend"), json=json, headers=headers)
            if self.lose_ack and url.endswith("/report") and json["points"]:
                self.lose_ack = False
                raise ConnectionError("Reply lost after database commit")
            return result

    http = FlakyHttp()
    path = tmp_path / "connector" / "delivery.json"
    poster = BackendPoster("http://backend", "researcher_a", PROJECT, "solver.log", http, state_file=path)
    poster.samples([sample_of("step 1 energy = 2")])
    pending = poster._reports.pending
    assert pending and path.is_file()
    restored = BackendPoster("http://backend", "researcher_a", PROJECT, "solver.log", http, state_file=path)
    assert restored._reports.pending == pending
    restored.samples([sample_of("step 2 energy = 3")])
    restored.new_run()
    restored.samples([sample_of("step 1 energy = 4")])
    runs = client.get("/simulation-runs", headers=auth).json()
    assert len(runs) == 2
    details = [client.get("/simulation-runs/" + run["id"], headers=auth).json() for run in runs]
    assert sorted(len(run["series"]["energy"]) for run in details) == [1, 2]
    assert "Authorization" not in path.read_text(encoding="utf-8")


def test_creao_context_tool_roundtrip_excludes_private_investigations_and_proposes_only(lab):
    from backend.memory.private_memory import PrivateMemory

    client, auth, fake, data = lab
    run_id, anomaly_id, _ = watched_run(client, auth)
    PrivateMemory(Database(data), "researcher_a").add_episode(PROJECT, "diagnosis", "Private result",
        details=json.dumps({"simulation_run_ref": "run:" + run_id, "reply": "PRIVATE_UNPUBLISHED_MARKER"}))
    requested = [{"tool": "get_research_context", "ref": "run:" + run_id},
                 {"tool": "propose_research_relationship", "source_ref": "anomaly:" + anomaly_id,
                  "predicate": "associated_with", "target_ref": "run:" + run_id}]

    def run(inputs):
        fake.inputs.append(inputs)
        reply = "```json\n" + json.dumps({"tool_requests": requested}) + "\n```" if len(fake.inputs) == 1 else "The saved run records a non-finite residual."
        return SimpleNamespace(run_id="provider-" + str(len(fake.inputs)), reply=reply, issues=[], summary={}, analysis_ran=False)

    fake.run = run
    started = client.post("/personal-agent/chat", headers=auth, json={"project_id": PROJECT, "message": "What happened in this run?"})
    assert started.status_code == 202, started.text
    job = client.get("/jobs/" + started.json()["job_id"], headers=auth).json()
    assert job["status"] == "completed", job
    assert len(fake.inputs) == 2
    assert "tool_results" in fake.inputs[1]["observed_symptom"]
    assert "run:" + run_id in fake.inputs[1]["observed_symptom"]
    assert "PRIVATE_UNPUBLISHED_MARKER" not in fake.inputs[1]["observed_symptom"]
    call = job["diagnosis"]["tool_calls"][1]
    assert call["ok"], call
    # Chat history deliberately retains call summaries, not entire tool payloads.
    with closing(Database(data).private("researcher_a")) as conn:
        row = conn.execute("SELECT status FROM context_links WHERE source_ref=? AND target_ref=?",
                           ("anomaly:" + anomaly_id, "run:" + run_id)).fetchone()
    assert row["status"] == "proposed"
