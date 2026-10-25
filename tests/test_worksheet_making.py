"""Making worksheets: numbered per researcher (Worksheet 1, 2, ...), asked of the Super Brain with
what to cover or written by the leader for it to finish, teaching what's new to each researcher,
cancelled and restored one at a time. CREAO is faked."""
import json
import uuid
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
EARLIER = "2026-01-01T00:00:00+00:00"


class Creao:
    def __init__(self):
        self.replies, self.calls = [], []

    def run(self, inputs):
        self.calls.append(inputs)
        return SimpleNamespace(reply=self.replies.pop(0), run_id=f"run-{len(self.calls)}") if self.replies else DIAGNOSIS


@pytest.fixture
def lab(tmp_path):
    creao = Creao()
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda slot: creao, data_dir=data, chat_setup=lambda: ChatSettings("CHAT", "test")))
    headers = {m: {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': m}).json()['token']}"}
               for m in ("researcher_a", "researcher_b", "researcher_c", "leader")}
    lab = SimpleNamespace(client=client, creao=creao, headers=headers, db=Database(data))
    lab.fa = publish(lab, "researcher_a", "Time step above the stability limit", "Cause: r = 0.30 > 0.25.")
    lab.fb = publish(lab, "researcher_b", "Grid refinement needs a smaller time step", "Cause: r grows as 1/dx^2.")
    return lab


def publish(lab, member, title, summary):
    job_id = lab.client.post("/diagnose", json={"project_id": "heat-diffusion", "symptom": "blow-up", "use_super_brain": False},
                             headers=lab.headers[member]).json()["job_id"]
    draft_id = lab.client.get(f"/diagnose/{job_id}", headers=lab.headers[member]).json()["draft_id"]
    return lab.client.post(f"/drafts/{draft_id}/approve", json={"title": title, "summary": summary}, headers=lab.headers[member]).json()


def sheet(member, topic, *finding_ids, title=None):
    return {"member_id": member, "title": title or f"For {member}", "items": [
        {"kind": "question", "topic": topic, "prompt": f"About {topic}?", "model_answer": "Because.", "finding_ids": list(finding_ids)}]}


def make(lab, sheets, **body):
    lab.creao.replies = ["```json\n" + json.dumps({"worksheets": sheets}) + "\n```"]
    started = lab.client.post("/worksheets", json={"project_id": "heat-diffusion", **body}, headers=lab.headers["leader"])
    if started.status_code != 202:
        return started, None
    return started, lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["leader"]).json()


def first_round(lab):
    _, job = make(lab, [sheet("researcher_a", "Grid refinement", lab.fb["id"]),
                        sheet("researcher_b", "Stability limit", lab.fa["id"]),
                        sheet("researcher_c", "Both", lab.fa["id"], lab.fb["id"])])
    # Everything so far happened before these worksheets were made (timestamps have one-second resolution).
    with closing(lab.db.shared()) as conn, conn:
        conn.execute("UPDATE shared_findings SET created_at = ?", (EARLIER,))
        conn.execute("UPDATE worksheet_weeks SET made_at = '2026-01-01T00:00:10+00:00'")
    return job


def sheets(lab, member):
    return lab.client.get("/worksheets?project_id=heat-diffusion", headers=lab.headers[member]).json()


def numbers(lab, member, viewer="leader"):
    """(number, title, cancelled) of one researcher's worksheets, oldest first."""
    return [(s["number"], s["title"], s["cancelled"]) for s in reversed(sheets(lab, viewer)) if s["member_id"] == member]


def upcoming(lab, only_new=True):
    plan = lab.client.get(f"/worksheets/next?project_id=heat-diffusion&only_new={str(only_new).lower()}",
                          headers=lab.headers["leader"]).json()
    return {p["member_id"]: p["new_findings"] for p in plan["people"]}


def context(lab):
    return json.loads(lab.creao.calls[-1]["context"].split("\n\n", 1)[1])


def test_each_researchers_worksheets_are_numbered_and_a_cancelled_number_is_used_again(lab):
    job = first_round(lab)
    assert len(job["diagnosis"]["worksheet_ids"]) == 3 and "week" not in job["diagnosis"]
    assert {s["member_id"]: s["number"] for s in sheets(lab, "leader")} == {"researcher_a": 1, "researcher_b": 1, "researcher_c": 1}

    make(lab, [sheet("researcher_a", "Boundaries", title="Second for A")], member_ids=["researcher_a"], request="Boundary rows")
    assert numbers(lab, "researcher_a") == [(1, "For researcher_a", False), (2, "Second for A", False)]
    second = next(s["id"] for s in sheets(lab, "leader") if s["title"] == "Second for A")

    assert lab.client.post(f"/worksheets/{second}/cancel", headers=lab.headers["leader"]).json()["cancelled"] is True
    assert [s["number"] for s in sheets(lab, "researcher_a")] == [1]  # hidden from A
    assert lab.client.get(f"/worksheets/{second}", headers=lab.headers["researcher_a"]).status_code == 404
    make(lab, [sheet("researcher_a", "Again", title="Redo for A")], member_ids=["researcher_a"], request="Boundary rows again")
    assert numbers(lab, "researcher_a") == [(1, "For researcher_a", False), (2, "Second for A", True), (2, "Redo for A", False)]

    lab.client.post(f"/worksheets/{second}/restore", headers=lab.headers["leader"])
    assert numbers(lab, "researcher_a") == [(1, "For researcher_a", False), (2, "Second for A", False), (3, "Redo for A", False)]
    assert [s["number"] for s in sheets(lab, "researcher_a")] == [3, 2, 1]  # newest first


def test_asking_the_super_brain_with_a_request_works_even_without_new_findings(lab):
    first_round(lab)
    assert upcoming(lab) == {"researcher_a": 0, "researcher_b": 0, "researcher_c": 0}
    refused, _ = make(lab, [])  # nothing new, nothing asked
    assert refused.status_code == 422 and "Nothing new to teach" in refused.json()["detail"]

    started, job = make(lab, [sheet("researcher_c", "Boundary conditions")], member_ids=["researcher_c"],
                        request="Boundary conditions for the heat equation. password = HUNTER2")
    assert started.status_code == 202 and [r["member_id"] for r in started.json()["researchers"]] == ["researcher_c"]
    sent = context(lab)
    assert sent["researchers"][0]["learn_from"] == [] and sent["researchers"][0]["already_covered"] == ["Both"]
    assert sent["leader_request"].startswith("Boundary conditions for the heat equation") and "HUNTER2" not in sent["leader_request"]
    assert "leader_request" in lab.creao.calls[-1]["context"] and "follow it" in lab.creao.calls[-1]["context"]
    assert numbers(lab, "researcher_c")[-1][:1] == (2,)
    assert lab.client.post("/worksheets", json={"project_id": "heat-diffusion", "member_ids": [], "request": "Anything"},
                           headers=lab.headers["leader"]).json()["detail"] == "Pick at least one researcher in this project."


def test_the_leaders_own_questions_and_title_are_sent_for_the_super_brain_to_finish(lab):
    started, job = make(lab, [sheet("researcher_b", "Stability", lab.fa["id"])], member_ids=["researcher_b"],
                        title="Stability check", request="Keep it short.", questions=[
                            {"kind": "question", "prompt": "What is the stability limit for r?"},
                            {"kind": "try", "prompt": "Halve dx in a copy and predict when it fails.", "model_answer": "Around step 600."},
                        ])
    assert started.status_code == 202 and job["status"] == "completed"
    sent = context(lab)
    assert sent["leader_questions"] == [
        {"kind": "question", "prompt": "What is the stability limit for r?", "model_answer": ""},
        {"kind": "try", "prompt": "Halve dx in a copy and predict when it fails.", "model_answer": "Around step 600."}]
    assert "keep every one, in order" in lab.creao.calls[-1]["context"]
    [b] = sheets(lab, "researcher_b")
    assert b["title"] == "Stability check" and b["number"] == 1  # the leader's title, not the Super Brain's

    bad = lab.client.post("/worksheets", json={"project_id": "heat-diffusion", "questions": [{"kind": "essay", "prompt": "x"}]},
                          headers=lab.headers["leader"])
    assert bad.status_code == 422
    empty = lab.client.post("/worksheets", json={"project_id": "heat-diffusion", "questions": [{"prompt": ""}]},
                            headers=lab.headers["leader"])
    assert empty.status_code == 422


def test_a_worksheet_from_the_leaders_questions_is_exactly_those_with_the_super_brains_answers(lab):
    # As the real agent did: it kept the leader's items but added its own, here first and slightly reworded.
    reply = {"member_id": "researcher_b", "title": "Ignored", "items": [
        {"kind": "question", "topic": "Extra", "prompt": "An extra question nobody asked for?", "model_answer": "X.",
         "finding_ids": [lab.fa["id"]]},
        {"kind": "try", "topic": "Grid refinement", "prompt": "In a copy, halve dx and keep dt; predict when it fails.",
         "model_answer": "Its own answer.", "finding_ids": [lab.fa["id"]]},
        {"kind": "question", "topic": "Time step stability", "prompt": "What is the stability limit for r?",
         "model_answer": "r <= 0.25, from the 2D FTCS amplification factor.", "finding_ids": [lab.fa["id"], "not-allowed"]},
        {"kind": "try", "topic": "Extra", "prompt": "Another step the leader never wrote.", "model_answer": "Y.", "finding_ids": []},
    ]}
    lab.creao.replies = ["```json\n" + json.dumps({"worksheets": [reply]}) + "\n```"]
    started = lab.client.post("/worksheets", headers=lab.headers["leader"], json={
        "project_id": "heat-diffusion", "member_ids": ["researcher_b"], "only_new": False, "title": "Stability check",
        "questions": [{"kind": "question", "prompt": "What is the stability limit for r?"},
                      {"kind": "try", "prompt": "In a copy, halve dx and keep dt. Predict when it fails.",
                       "model_answer": "Around step 600."},
                      {"kind": "question", "prompt": "Explain grid-specific safety in your own words, in one paragraph."}]})
    lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["leader"])
    [b] = sheets(lab, "researcher_b")
    items = lab.client.get(f"/worksheets/{b['id']}", headers=lab.headers["leader"]).json()["items"]
    assert [(i["kind"], i["prompt"]) for i in items] == [
        ("question", "What is the stability limit for r?"),
        ("try", "In a copy, halve dx and keep dt. Predict when it fails."),  # the leader's words, not the agent's
        ("question", "Explain grid-specific safety in your own words, in one paragraph.")]
    assert [i["model_answer"] for i in items] == ["r <= 0.25, from the 2D FTCS amplification factor.", "Around step 600.", ""]
    assert [i["topic"] for i in items] == ["Time step stability", "Grid refinement", "General"]
    assert [[f["id"] for f in i["findings"]] for i in items] == [[lab.fa["id"]], [lab.fa["id"]], []]
    assert b["title"] == "Stability check" and b["items"] == 3


def test_long_text_is_cut_at_a_sentence_never_mid_word():
    from backend.knowledge_sharing.teacher import LIMITS, _text
    answer = " ".join(f"Sentence number {n} explains one more step of the stability argument." for n in range(60))
    cut = _text(answer, "model_answer")
    assert len(cut) <= LIMITS["model_answer"] and cut.endswith(".…")
    assert cut[:-1] in answer  # nothing invented, just shortened
    one_word_run = _text("x" * 900, "prompt")
    assert len(one_word_run) <= LIMITS["prompt"] and one_word_run.endswith("…")
    assert _text("Short and sweet.", "model_answer") == "Short and sweet."


def test_new_findings_and_correction_notes_are_taught_next_and_everything_when_asked(lab):
    first_round(lab)
    fb2 = publish(lab, "researcher_b", "Boundary rows are never updated", "Cause: the edges are copied, not computed.")
    assert upcoming(lab) == {"researcher_a": 1, "researcher_b": 0, "researcher_c": 1}
    make(lab, [sheet("researcher_a", "Boundaries", fb2["id"])], member_ids=["researcher_a"])
    [a] = context(lab)["researchers"]
    assert a["learn_from"] == [fb2["id"]] and a["already_covered"] == ["Grid refinement"]
    assert [f["id"] for f in context(lab)["findings"]] == [fb2["id"]]  # old findings aren't sent again
    assert "earlier worksheets" in lab.creao.calls[-1]["context"] and "week" not in lab.creao.calls[-1]["context"]

    lab.client.post(f"/findings/{lab.fa['id']}/notes", json={"kind": "correction", "text": "It was r = 0.40, not 0.30."},
                    headers=lab.headers["leader"])
    assert upcoming(lab)["researcher_b"] == 1
    assert upcoming(lab, only_new=False) == {"researcher_a": 2, "researcher_b": 1, "researcher_c": 3}  # never their own


def test_a_cancelled_worksheets_lessons_come_back_next_time(lab):
    first_round(lab)
    b = next(s["id"] for s in sheets(lab, "leader") if s["member_id"] == "researcher_b")
    lab.client.post(f"/worksheets/{b}/cancel", headers=lab.headers["leader"])
    assert sheets(lab, "researcher_b") == [] and upcoming(lab)["researcher_b"] == 1
    lab.client.post(f"/worksheets/{b}/restore", headers=lab.headers["leader"])
    assert len(sheets(lab, "researcher_b")) == 1 and upcoming(lab)["researcher_b"] == 0


def test_worksheets_cancelled_with_their_week_can_each_be_restored(lab):
    first_round(lab)
    with closing(lab.db.shared()) as conn, conn:  # as a whole week was cancelled before weeks went away
        conn.execute("UPDATE worksheet_weeks SET cancelled_at = '2026-01-02T00:00:00+00:00'")
    assert all(s["cancelled"] for s in sheets(lab, "leader")) and sheets(lab, "researcher_a") == []
    a = next(s["id"] for s in sheets(lab, "leader") if s["member_id"] == "researcher_a")
    lab.client.post(f"/worksheets/{a}/restore", headers=lab.headers["leader"])
    assert [s["number"] for s in sheets(lab, "researcher_a")] == [1]
    assert sheets(lab, "researcher_b") == []  # the others stay cancelled


def test_only_the_leader_cancels_and_week_endpoints_are_gone(lab):
    job = first_round(lab)
    sheet_id = job["diagnosis"]["worksheet_ids"][0]
    assert lab.client.post(f"/worksheets/{sheet_id}/cancel", headers=lab.headers["researcher_a"]).status_code == 403
    assert lab.client.post("/worksheets/nope/cancel", headers=lab.headers["leader"]).status_code == 404
    assert lab.client.post("/worksheets/weeks/anything/cancel", headers=lab.headers["leader"]).status_code in (404, 405)
    assert lab.client.get("/worksheets/next?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).status_code == 403
    assert "week" not in lab.client.get("/worksheets/next?project_id=heat-diffusion", headers=lab.headers["leader"]).json()


def test_worksheets_made_before_batches_existed_are_numbered(lab):
    with closing(lab.db.shared()) as conn:
        conn.execute("INSERT INTO worksheets (id, workspace_id, project_id, member_id, created_by, title, creao_run_id, created_at)"
                     " VALUES (?, 'demo-lab', 'heat-diffusion', 'researcher_a', 'leader', 'Old one', 'old-run', ?)",
                     (str(uuid.uuid4()), EARLIER))
        conn.commit()
    [old] = sheets(lab, "researcher_a")
    assert old["number"] == 1 and old["title"] == "Old one" and old["cancelled"] is False
    make(lab, [sheet("researcher_a", "New", lab.fb["id"])], only_new=False)
    assert [s["number"] for s in sheets(lab, "researcher_a")] == [2, 1]
