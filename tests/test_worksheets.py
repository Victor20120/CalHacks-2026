"""The Super Brain's teacher: a worksheet per researcher from what the others found;
the researcher answers, the leader comments. CREAO is faked."""
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


class Creao:
    """The next scripted reply; with none, a diagnosis (used to publish findings)."""

    def __init__(self):
        self.replies, self.calls = [], []

    def run(self, inputs):
        self.calls.append(inputs)
        if not self.replies:
            return DIAGNOSIS
        return SimpleNamespace(reply=self.replies.pop(0), run_id="teacher-run")


@pytest.fixture
def lab(tmp_path):
    creao = Creao()
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda slot: creao, data_dir=data,
                                   chat_setup=lambda: ChatSettings("CHAT", "test")))
    headers = {m: {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': m}).json()['token']}"}
               for m in ("researcher_a", "researcher_b", "researcher_c", "leader")}
    return SimpleNamespace(client=client, creao=creao, headers=headers, db=Database(data))


def publish(lab, member, title, summary, project="heat-diffusion"):
    job_id = lab.client.post("/diagnose", json={"project_id": project, "symptom": "the run blows up", "use_super_brain": False},
                             headers=lab.headers[member]).json()["job_id"]
    draft_id = lab.client.get(f"/diagnose/{job_id}", headers=lab.headers[member]).json()["draft_id"]
    return lab.client.post(f"/drafts/{draft_id}/approve", json={"title": title, "summary": summary},
                           headers=lab.headers[member]).json()


def item(kind, prompt, ids, topic="Time step stability", answer="Because r > 0.25."):
    return {"kind": kind, "topic": topic, "prompt": prompt, "model_answer": answer, "finding_ids": ids}


def reply(*worksheets):
    return "Here you go.\n```json\n" + json.dumps({"worksheets": list(worksheets)}) + "\n```"


@pytest.fixture
def two_findings(lab):
    a = publish(lab, "researcher_a", "Explicit time step above the CFL limit makes the heat solver blow up",
                "Symptom: max|T| reaches inf. Cause: r = 0.30 > 0.25. Fix: dt = 0.02.")
    b = publish(lab, "researcher_b", "Refining the grid without shrinking dt brings the blow-up back",
                "Symptom: blow-up after halving dx. Cause: r scales with 1/dx^2.")
    return a, b


def make(lab, worksheets_reply, project="heat-diffusion"):
    lab.creao.replies = [worksheets_reply]
    started = lab.client.post("/worksheets", json={"project_id": project}, headers=lab.headers["leader"])
    job = lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["leader"]).json() if started.status_code == 202 else None
    return started, job


def sheets(lab, member, project="heat-diffusion"):
    return lab.client.get(f"/worksheets?project_id={project}", headers=lab.headers[member]).json()


def test_one_worksheet_per_researcher_teaching_what_the_others_found(lab, two_findings):
    a, b = two_findings
    started, job = make(lab, reply(
        {"member_id": "researcher_a", "title": "Grid refinement and dt", "items": [
            item("question", "Why does halving dx bring the blow-up back?", [b["id"], a["id"]]),
            item("try", "In a copy, halve dx and keep dt. Predict the step where it fails, then click Run.", [b["id"]]),
            item("essay", "Not a kind we use", [b["id"]]),
            item("question", "", [b["id"]]),
        ]},
        {"member_id": "researcher_a", "title": "A second one for A", "items": [item("question", "Ignored", [b["id"]])]},
        {"member_id": "researcher_b", "items": [item("question", f"Q{n}", [a["id"]]) for n in range(12)]},
        {"member_id": "researcher_c", "title": "Both lessons", "items": [item("question", "What is r?", [a["id"], b["id"]])]},
        {"member_id": "outsider", "title": "Not in this project", "items": [item("question", "?", [a["id"]])]},
    ))
    assert started.status_code == 202
    assert {r["member_id"]: r["findings"] for r in started.json()["researchers"]} == {
        "researcher_a": 1, "researcher_b": 1, "researcher_c": 2}
    assert job["status"] == "completed" and len(job["diagnosis"]["worksheet_ids"]) == 3

    context = json.loads(lab.creao.calls[-1]["context"].split("\n\n", 1)[1])
    learn = {r["member_id"]: r["learn_from"] for r in context["researchers"]}
    assert learn == {"researcher_a": [b["id"]], "researcher_b": [a["id"]], "researcher_c": [b["id"], a["id"]]}
    assert "OTHER researchers" in lab.creao.calls[-1]["context"]
    assert "Never tell them to change their real project" in lab.creao.calls[-1]["context"]

    assert len(sheets(lab, "leader")) == 3
    [mine] = sheets(lab, "researcher_a")
    assert mine["title"] == "Grid refinement and dt" and mine["status"] == "assigned" and mine["items"] == 2
    sheet = lab.client.get(f"/worksheets/{mine['id']}", headers=lab.headers["researcher_a"]).json()
    assert [i["kind"] for i in sheet["items"]] == ["question", "try"]
    assert [f["id"] for f in sheet["items"][0]["findings"]] == [b["id"]]  # A's own finding isn't taught to A
    [b_sheet] = sheets(lab, "researcher_b")
    assert b_sheet["items"] == 8  # capped
    assert b_sheet["title"] == "What the group learned, for Researcher B"


def test_only_the_leader_makes_worksheets_and_only_with_something_to_teach(lab):
    assert lab.client.post("/worksheets", json={"project_id": "heat-diffusion"}, headers=lab.headers["researcher_a"]).status_code == 403
    nothing = lab.client.post("/worksheets", json={"project_id": "heat-diffusion"}, headers=lab.headers["leader"])
    assert nothing.status_code == 422 and "Nothing to teach yet" in nothing.json()["detail"]
    publish(lab, "researcher_a", "Wave solver blows up when the Courant number exceeds 1", "c dt / dx = 1.2.", project="wave-propagation")
    alone = lab.client.post("/worksheets", json={"project_id": "wave-propagation"}, headers=lab.headers["leader"])
    assert alone.status_code == 422  # A is the only researcher there, and can't learn from themselves
    assert lab.creao.calls[-1].get("context") is None  # no Super Brain run was started


def test_worksheets_use_only_the_projects_findings(lab, two_findings):
    publish(lab, "researcher_a", "Wave solver blows up when the Courant number exceeds 1", "Restricted wave detail.",
            project="wave-propagation")
    make(lab, reply())
    assert "Wave solver" not in lab.creao.calls[-1]["context"] and "Restricted wave detail" not in lab.creao.calls[-1]["context"]


def test_researchers_see_only_their_own_and_model_answers_after_review(lab, two_findings):
    a, b = two_findings
    make(lab, reply({"member_id": "researcher_a", "items": [item("question", "Why?", [b["id"]], answer="MODEL ANSWER")]},
                    {"member_id": "researcher_b", "items": [item("question", "Why?", [a["id"]])]}))
    [mine] = sheets(lab, "researcher_a")
    assert lab.client.get(f"/worksheets/{mine['id']}", headers=lab.headers["researcher_a"]).json()["items"][0]["model_answer"] is None
    assert lab.client.get(f"/worksheets/{mine['id']}", headers=lab.headers["leader"]).json()["items"][0]["model_answer"] == "MODEL ANSWER"
    assert lab.client.get(f"/worksheets/{mine['id']}", headers=lab.headers["researcher_b"]).status_code == 404
    assert [s["member_id"] for s in sheets(lab, "researcher_b")] == ["researcher_b"]
    assert sheets(lab, "researcher_c") == []


def test_answer_submit_then_the_leader_comments(lab, two_findings):
    _, b = two_findings
    make(lab, reply({"member_id": "researcher_a", "items": [item("question", "Why?", [b["id"]], answer="MODEL"),
                                                            item("try", "Halve dx in a copy.", [b["id"]])]}))
    [mine] = sheets(lab, "researcher_a")
    sheet = lab.client.get(f"/worksheets/{mine['id']}", headers=lab.headers["researcher_a"]).json()
    q, t = (i["id"] for i in sheet["items"])
    path = f"/worksheets/{mine['id']}"

    saved = lab.client.put(f"{path}/answers", json={"answers": {q: "r grows as 1/dx^2. password = HUNTER2", "nope": "x"}},
                           headers=lab.headers["researcher_a"]).json()
    assert saved["status"] == "assigned" and "HUNTER2" not in saved["items"][0]["answer"]
    assert lab.client.put(f"{path}/answers", json={"answers": {q: "x"}}, headers=lab.headers["leader"]).status_code == 404
    submitted = lab.client.put(f"{path}/answers", json={"answers": {t: "It failed at step 600."}, "submit": True},
                               headers=lab.headers["researcher_a"]).json()
    assert submitted["status"] == "submitted" and submitted["submitted_at"]
    assert sheets(lab, "leader")[0]["answered"] == 2

    assert lab.client.post(f"{path}/review", json={"comment": "x"}, headers=lab.headers["researcher_a"]).status_code == 403
    reviewed = lab.client.post(f"{path}/review", json={"comments": {q: "Right, and say why dt must shrink too."},
                                                       "comment": "Good work."}, headers=lab.headers["leader"]).json()
    assert reviewed["status"] == "reviewed" and reviewed["leader_comment"] == "Good work."
    seen = lab.client.get(path, headers=lab.headers["researcher_a"]).json()
    assert seen["items"][0]["leader_comment"] == "Right, and say why dt must shrink too."
    assert seen["items"][0]["model_answer"] == "MODEL"  # shown once the leader has reviewed it
    locked = lab.client.put(f"{path}/answers", json={"answers": {q: "changed"}}, headers=lab.headers["researcher_a"])
    assert locked.status_code == 409


def test_only_the_leader_deletes_and_a_bad_reply_makes_nothing(lab, two_findings):
    _, b = two_findings
    make(lab, reply({"member_id": "researcher_a", "items": [item("question", "Why?", [b["id"]])]}))
    [mine] = sheets(lab, "researcher_a")
    assert lab.client.delete(f"/worksheets/{mine['id']}", headers=lab.headers["researcher_a"]).status_code == 403
    assert lab.client.delete(f"/worksheets/{mine['id']}", headers=lab.headers["leader"]).status_code == 200
    assert sheets(lab, "leader") == []
    with closing(lab.db.shared()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM worksheet_items").fetchone()[0] == 0

    _, job = make(lab, "I could not write worksheets today.")
    assert job["status"] == "completed" and job["diagnosis"]["worksheet_ids"] == []
    assert sheets(lab, "leader") == []
