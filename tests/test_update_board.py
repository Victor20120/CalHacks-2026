"""The Super Brain's update board: the run a finding came from, and comments between the
leader and the researchers. CREAO is faked."""
import json
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings, parse_run
from backend.db.database import Database

FIXTURES = Path(__file__).parent / "fixtures"
DIAGNOSIS = parse_run(json.loads((FIXTURES / "creao_personal_agent_diagnosis.json").read_text(encoding="utf-8"))["final_response"])
ALERT = {"severity": "critical", "check": "nan_inf", "message": "max|T| became inf at step 2223 (line 24)",
         "metric": "max|T|", "line_number": 24, "line": "step  2223  max|T| = inf", "step": 2223}


class Creao:
    def __init__(self):
        self.replies = []

    def run(self, inputs):
        return SimpleNamespace(reply=self.replies.pop(0), run_id="chat-run") if self.replies else DIAGNOSIS


@pytest.fixture
def lab(tmp_path):
    creao = Creao()
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda slot: creao, data_dir=data,
                                   chat_setup=lambda: ChatSettings("CHAT", "test")))
    headers = {m: {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': m}).json()['token']}"}
               for m in ("researcher_a", "researcher_b", "leader")}
    return SimpleNamespace(client=client, creao=creao, headers=headers, db=Database(data))


def watched_run(lab, member="researcher_a", project="heat-diffusion"):
    run = lab.client.post("/simulation-runs", json={"project_id": project, "run_label": "python heat2d.py config.json"},
                          headers=lab.headers[member]).json()
    points = [{"step": s, "values": {"max|T|": v}} for s, v in ((100, 276.85), (200, 2.75e14), (2223, None))]
    lab.client.post(f"/simulation-runs/{run['id']}/report", json={"points": points, "alerts": [ALERT], "stopped": True},
                    headers=lab.headers[member])
    return run


def draft(lab, member="researcher_a", project="heat-diffusion"):
    job_id = lab.client.post("/diagnose", json={"project_id": project, "symptom": "the run blows up", "use_super_brain": False},
                             headers=lab.headers[member]).json()["job_id"]
    return lab.client.get(f"/diagnose/{job_id}", headers=lab.headers[member]).json()["draft_id"]


def approve(lab, draft_id, member="researcher_a", **body):
    return lab.client.post(f"/drafts/{draft_id}/approve", json={"title": "CFL stability limit violated (r = 0.30 > 0.25)",
                                                                **body}, headers=lab.headers[member])


# --- The run a finding came from --------------------------------------------------------------


def test_a_run_attached_when_sharing_shows_for_those_who_may_see_it(lab):
    run = watched_run(lab)
    finding = approve(lab, draft(lab), run_id=run["id"]).json()
    assert finding["run_id"] == run["id"]
    [listed] = lab.client.get("/superbrain/findings?project_id=heat-diffusion", headers=lab.headers["leader"]).json()
    assert listed["run_id"] == run["id"]
    assert lab.client.get(f"/simulation-runs/{run['id']}", headers=lab.headers["leader"]).status_code == 200
    assert lab.client.get(f"/simulation-runs/{run['id']}", headers=lab.headers["researcher_b"]).status_code == 404  # runs stay private


def test_only_your_own_run_in_the_same_project_can_be_attached(lab):
    theirs = watched_run(lab, member="researcher_b")
    elsewhere = watched_run(lab, project="wave-propagation")
    draft_id = draft(lab)
    for run in (theirs, elsewhere):
        refused = approve(lab, draft_id, run_id=run["id"])
        assert refused.status_code == 422 and "your own runs in this project" in refused.json()["detail"]
    assert approve(lab, draft_id, run_id="no-such-run").status_code == 422
    assert [d["id"] for d in lab.client.get("/drafts", headers=lab.headers["researcher_a"]).json()] == [draft_id]  # still private
    assert approve(lab, draft_id).json()["run_id"] is None  # sharing without a run still works


def test_a_chat_share_carries_the_run_the_agent_looked_at(lab):
    run = watched_run(lab)
    theirs = watched_run(lab, member="researcher_b")
    share = {"title": "Time step too large for the grid", "summary": "r = 0.30 > 0.25 in the run that blew up at step 2223."}
    for looked_at, attached in ((run["id"], run["id"]), (theirs["id"], None)):
        share["title"] += "!"  # a new title each time, so it's shared again
        lab.creao.replies = [
            "```json\n" + json.dumps({"tool_requests": [{"tool": "get_simulation_runs", "run_id": looked_at}]}) + "\n```",
            "It blew up from the time step.\n```json\n" + json.dumps({"share": share}) + "\n```",
        ]
        job_id = lab.client.post("/personal-agent/chat", json={"project_id": "heat-diffusion", "message": "Why?"},
                                 headers=lab.headers["researcher_a"]).json()["job_id"]
        job = lab.client.get(f"/jobs/{job_id}", headers=lab.headers["researcher_a"]).json()
        finding = lab.client.get(f"/findings/{job['diagnosis']['shared_finding']['id']}", headers=lab.headers["leader"]).json()
        assert finding["finding"]["run_id"] == attached  # B's run was never visible to A's agent, so nothing is attached


# --- Comments between the leader and the researchers -------------------------------------------


def test_the_leader_and_the_researcher_write_and_see_one_thread(lab):
    finding = approve(lab, draft(lab)).json()
    path = f"/findings/{finding['id']}/comments"
    first = lab.client.post(path, json={"text": "Good catch. Rerun with dt = 0.02?"}, headers=lab.headers["leader"])
    assert first.status_code == 201 and first.json()["role"] == "leader" and first.json()["member_name"] == "Group Leader"
    lab.client.post(path, json={"text": "Done: stable for 3000 steps."}, headers=lab.headers["researcher_a"])
    lab.client.post(path, json={"text": "Same on my grid."}, headers=lab.headers["researcher_b"])

    for member in ("leader", "researcher_a", "researcher_b"):
        thread = lab.client.get(path, headers=lab.headers[member]).json()
        assert [c["member_id"] for c in thread] == ["leader", "researcher_a", "researcher_b"]
    [listed] = lab.client.get("/superbrain/findings?project_id=heat-diffusion", headers=lab.headers["researcher_b"]).json()
    assert listed["comments"] == 3
    assert len(lab.client.get(f"/findings/{finding['id']}", headers=lab.headers["leader"]).json()["comments"]) == 3


def test_comments_follow_project_access_and_are_checked(lab):
    wave = approve(lab, draft(lab, project="wave-propagation")).json()
    path = f"/findings/{wave['id']}/comments"
    assert lab.client.post(path, json={"text": "Can I see this?"}, headers=lab.headers["researcher_b"]).status_code == 404
    assert lab.client.get(path, headers=lab.headers["researcher_b"]).status_code == 404
    assert lab.client.post(path, json={"text": " "}, headers=lab.headers["leader"]).status_code == 422
    assert lab.client.post(path, json={"text": "x" * 2001}, headers=lab.headers["leader"]).status_code == 422
    assert lab.client.post(path, json={"text": "hi"}).status_code == 401
    saved = lab.client.post(path, json={"text": "Use token = SECRET123 to rerun"}, headers=lab.headers["leader"]).json()
    assert "SECRET123" not in saved["text"]
    with closing(lab.db.shared()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM finding_comments").fetchone()[0] == 1
