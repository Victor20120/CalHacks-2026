"""The last item of the prototype definition of done: provider credentials are
never exposed, and source files are never changed without approval."""
import hashlib
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import CreaoClient, personal_agent
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service
from local_connector.simulation_monitor.watcher import LogWatcher

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
FAKE = {f"CREAO_{slot}_{kind}": f"capi_FAKE_{slot.lower()}_{kind.lower()}_value"
        for slot in ("HUB", "AGENT_1", "AGENT_2", "AGENT_3") for kind in ("KEY", "APP_ID")}


class RejectingSession:
    """CREAO answering 401 with an error body that echoes the key."""

    def request(self, method, url, headers=None, **kwargs):
        class Response:
            status_code = 401
            text = f"bad key {headers['Authorization']}"

            def json(self):
                return {"error": "AUTHENTICATION_REQUIRED"}

        return Response()


@pytest.fixture
def fake_keys(monkeypatch):
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)


def assert_no_credentials(text):
    for value in FAKE.values():
        assert value not in text


def test_no_backend_response_contains_a_creao_key_or_app_id(tmp_path, fake_keys):
    client = TestClient(create_app(
        client_factory=lambda prefix: CreaoClient(personal_agent(prefix, env_file=None), session=RejectingSession()),
        data_dir=tmp_path / "data",
    ))
    responses = [client.get("/health"), client.get("/demo/members"), client.get("/policy")]
    for member in ("researcher_a", "leader"):
        token = client.post("/sign-in", json={"member_id": member}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        responses += [client.get(p, headers=h) for p in (
            "/me", "/drafts", "/settings", "/alerts", "/alerts/history", "/members", "/offboarding",
            "/projects/heat-diffusion/findings", "/superbrain/search?q=blows+up")]
        if member == "researcher_a":
            started = client.post("/diagnose", headers=h, json={"project_id": "heat-diffusion", "symptom": "blows up"})
            job = client.get(f"/diagnose/{started.json()['job_id']}", headers=h)
            responses += [started, job]

    assert job.json()["status"] == "failed"  # CREAO rejected the key...
    assert "CREAO_AGENT_1_KEY" in job.json()["error"]["message"]  # ...and the error names the setting, not the value
    for response in responses:
        assert_no_credentials(response.text)


def test_the_dashboard_never_handles_keys():
    for path in (REPO / "frontend" / "src").rglob("*"):
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            assert not re.search(r"capi_|cr_sk_|CREAO_[A-Z0-9_]*KEY|\.env\b|import\.meta\.env", source), path


def _hashes(folder: Path, skip: set[str]) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file() and p.name not in skip}


def test_a_full_connector_session_leaves_every_project_file_unchanged(tmp_path):
    project = tmp_path / "heat_project"
    project.mkdir()
    for name in ("heat2d.py", "config.json", "run.log"):
        (project / name).write_bytes((FIXTURES / "sample_project" / name).read_bytes())
    (project / ".env").write_text("CREAO_HUB_KEY=capi_FAKE_decoy\n")
    before = _hashes(project, skip={"live.log"})

    registry = GrantRegistry(state_file=tmp_path / "home" / "grants.json")
    service = TestClient(create_service(registry, http=object(), watcher_factory=lambda p, poster: LogWatcher(
        p, lambda alerts: None, poll_seconds=0.02)))
    root = {"root": str(project)}
    service.post("/grants", json=root)
    service.post("/files", json=root)
    service.post("/evidence", json={**root, "selections": ["run.log", "config.json", "heat2d.py:18-25"]})
    service.post("/evidence", json={**root, "selections": [".env"]})  # refused
    watch = service.post("/watches", json={**root, "file": "live.log", "project_id": "heat-diffusion",
                                           "member_id": "researcher_a"}).json()
    (project / "live.log").write_text("step 1 max|T| = nan\n")  # the "simulation" writes its log
    time.sleep(0.2)
    service.delete(f"/watches/{watch['id']}")

    assert _hashes(project, skip={"live.log"}) == before
    assert (project / "live.log").read_text() == "step 1 max|T| = nan\n"  # the log itself is only read


def test_the_connector_writes_only_its_grants_file_and_saved_execution_copies():
    writers = re.compile(r"open\([^)]*['\"][wax]b?\+?['\"]|\.write_(text|bytes)\(|\.unlink\(|os\.remove|shutil\.(?:copy\w*|move|rmtree)|\.rename\(")
    offenders = {}
    for path in (REPO / "local_connector").rglob("*.py"):
        hits = writers.findall(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.relative_to(REPO).as_posix()] = len(hits)

    # Experiments and saved simulations write their own copies/logs below ~/.expandrg.
    # Their execution tests check the source project remains byte-for-byte unchanged.
    experiments = {p for p in offenders if p.startswith("local_connector/experiments/") or p in {
        "local_connector/simulations.py", "local_connector/context.py"}}
    assert {p: n for p, n in offenders.items() if p not in experiments} == {
        "local_connector/file_access/grants.py": 1}  # GrantRegistry._save, in ~/.expandrg
    grants_source = (REPO / "local_connector" / "file_access" / "grants.py").read_text(encoding="utf-8")
    assert "self.state_file.write_text" in grants_source
