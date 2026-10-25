"""Worksheets come in weeks: each teaches what's new to each researcher since their last worksheet,
builds on what they covered, and the leader can cancel a whole week or one person's. CREAO is faked."""
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


def sheet(member, topic, *finding_ids):
    return {"member_id": member, "title": f"For {member}", "items": [
        {"kind": "question", "topic": topic, "prompt": f"About {topic}?", "model_answer": "Because.", "finding_ids": list(finding_ids)}]}


def make(lab, sheets, **body):
    lab.creao.replies = ["```json\n" + json.dumps({"worksheets": sheets}) + "\n```"]
    started = lab.client.post("/worksheets", json={"project_id": "heat-diffusion", **body}, headers=lab.headers["leader"])
    if started.status_code != 202:
        return started, None
    return started, lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["leader"]).json()


def week_one(lab):
    _, job = make(lab, [sheet("researcher_a", "Grid refinement", lab.fb["id"]),
                        sheet("researcher_b", "Stability limit", lab.fa["id"]),
                        sheet("researcher_c", "Both", lab.fa["id"], lab.fb["id"])])
    # Everything so far happened before week 1 was made (timestamps have one-second resolution).
    with closing(lab.db.shared()) as conn, conn:
        conn.execute("UPDATE shared_findings SET created_at = ?", (EARLIER,))
        conn.execute("UPDATE worksheet_weeks SET made_at = '2026-01-01T00:00:10+00:00'")
    return job


def sheets(lab, member):
    return lab.client.get("/worksheets?project_id=heat-diffusion", headers=lab.headers[member]).json()


def upcoming(lab, only_new=True):
    plan = lab.client.get(f"/worksheets/next?project_id=heat-diffusion&only_new={str(only_new).lower()}",
                          headers=lab.headers["leader"]).json()
    return plan, {p["member_id"]: p["new_findings"] for p in plan["people"]}


def last_context(lab):
    return json.loads(lab.creao.calls[-1]["context"].split("\n\n", 1)[1])


def test_weeks_are_numbered_and_a_week_with_nothing_new_is_refused(lab):
    job = week_one(lab)
    assert job["diagnosis"]["week"] == 1 and len(job["diagnosis"]["worksheet_ids"]) == 3
    assert {s["week"] for s in sheets(lab, "leader")} == {1}
    plan, new = upcoming(lab)
    assert plan["week"] == 2 and new == {"researcher_a": 0, "researcher_b": 0, "researcher_c": 0}
    refused, _ = make(lab, [])
    assert refused.status_code == 422 and "Nothing new to teach" in refused.json()["detail"]


def test_next_week_teaches_only_whats_new_and_builds_on_covered_topics(lab):
    week_one(lab)
    fb2 = publish(lab, "researcher_b", "Boundary rows are never updated", "Cause: the edges are copied, not computed.")
    _, new = upcoming(lab)
    assert new == {"researcher_a": 1, "researcher_b": 0, "researcher_c": 1}
    started, job = make(lab, [sheet("researcher_a", "Boundaries", fb2["id"])], member_ids=["researcher_a"])
    assert started.json()["week"] == 2 and [r["member_id"] for r in started.json()["researchers"]] == ["researcher_a"]
    context = last_context(lab)
    assert context["week"] == 2
    [a] = context["researchers"]
    assert a["learn_from"] == [fb2["id"]] and a["already_covered"] == ["Grid refinement"]
    assert [f["id"] for f in context["findings"]] == [fb2["id"]]  # old findings aren't sent again
    assert "already_covered" in lab.creao.calls[-1]["context"] and "don't repeat them" in lab.creao.calls[-1]["context"]
    assert [(s["week"], s["member_id"]) for s in sheets(lab, "researcher_a")] == [(2, "researcher_a"), (1, "researcher_a")]
    assert [s["week"] for s in sheets(lab, "researcher_c")] == [1]  # C wasn't picked this week


def test_a_correction_note_makes_a_finding_new_again(lab):
    week_one(lab)
    lab.client.post(f"/findings/{lab.fa['id']}/notes", json={"kind": "correction", "text": "It was r = 0.40, not 0.30."},
                    headers=lab.headers["leader"])
    _, new = upcoming(lab)
    assert new == {"researcher_a": 0, "researcher_b": 1, "researcher_c": 1}


def test_everything_can_be_taught_again_when_only_new_is_off(lab):
    week_one(lab)
    plan, new = upcoming(lab, only_new=False)
    assert new == {"researcher_a": 1, "researcher_b": 1, "researcher_c": 2}
    started, _ = make(lab, [sheet("researcher_c", "Review", lab.fa["id"], lab.fb["id"])], only_new=False)
    assert started.status_code == 202 and len(last_context(lab)["researchers"]) == 3


def test_cancelling_a_week_hides_it_and_the_next_week_teaches_it_again(lab):
    week_one(lab)
    fb2 = publish(lab, "researcher_b", "Boundary rows are never updated", "Cause: copied edges.")
    _, job2 = make(lab, [sheet("researcher_a", "Boundaries", fb2["id"])])
    week2 = job2["diagnosis"]["week_id"]
    week2_sheet = job2["diagnosis"]["worksheet_ids"][0]

    assert lab.client.post(f"/worksheets/weeks/{week2}/cancel", headers=lab.headers["leader"]).json() == {"status": "cancelled"}
    assert [s["week"] for s in sheets(lab, "researcher_a")] == [1]  # week 2 is gone for A; week 1 stays
    assert lab.client.get(f"/worksheets/{week2_sheet}", headers=lab.headers["researcher_a"]).status_code == 404
    assert lab.client.put(f"/worksheets/{week2_sheet}/answers", json={"answers": {}}, headers=lab.headers["researcher_a"]).status_code == 404
    leader_view = {s["id"]: s for s in sheets(lab, "leader")}
    assert leader_view[week2_sheet]["cancelled"] == "week"
    plan, new = upcoming(lab)
    assert plan["week"] == 2 and new["researcher_a"] == 1  # what week 2 would have taught is new again

    lab.client.post(f"/worksheets/weeks/{week2}/restore", headers=lab.headers["leader"])
    assert [s["week"] for s in sheets(lab, "researcher_a")] == [2, 1]
    plan, new = upcoming(lab)
    assert plan["week"] == 3 and new["researcher_a"] == 0


def test_a_cancelled_weeks_number_is_used_again_and_the_cancelled_week_still_shows(lab):
    week1 = week_one(lab)["diagnosis"]["week_id"]
    lab.client.post(f"/worksheets/weeks/{week1}/cancel", headers=lab.headers["leader"])
    assert upcoming(lab)[0]["week"] == 1

    started, job = make(lab, [sheet("researcher_a", "Grid refinement again", lab.fb["id"])])
    redo = job["diagnosis"]["week_id"]
    assert started.json()["week"] == 1 and job["diagnosis"]["week"] == 1
    assert {(s["week_id"], s["week"], s["cancelled"]) for s in sheets(lab, "leader")} == {(redo, 1, None), (week1, 1, "week")}
    assert [(s["week_id"], s["week"]) for s in sheets(lab, "researcher_a")] == [(redo, 1)]
    assert upcoming(lab)[0]["week"] == 2

    lab.client.post(f"/worksheets/weeks/{week1}/restore", headers=lab.headers["leader"])
    assert {s["week_id"]: s["week"] for s in sheets(lab, "leader")} == {week1: 1, redo: 2}
    assert upcoming(lab)[0]["week"] == 3


def test_cancelling_one_persons_worksheet_leaves_the_others(lab):
    job = week_one(lab)
    by_member = {s["member_id"]: s["id"] for s in sheets(lab, "leader")}
    cancelled = lab.client.post(f"/worksheets/{by_member['researcher_b']}/cancel", headers=lab.headers["leader"]).json()
    assert cancelled["cancelled"] == "person"
    assert sheets(lab, "researcher_b") == []
    assert len(sheets(lab, "researcher_a")) == 1 and len(sheets(lab, "researcher_c")) == 1
    assert upcoming(lab)[1]["researcher_b"] == 1  # B's cancelled lesson comes back next week
    lab.client.post(f"/worksheets/{by_member['researcher_b']}/restore", headers=lab.headers["leader"])
    assert len(sheets(lab, "researcher_b")) == 1 and job["diagnosis"]["week"] == 1


def test_only_the_leader_cancels_and_unknown_weeks_are_refused(lab):
    job = week_one(lab)
    week_id, sheet_id = job["diagnosis"]["week_id"], job["diagnosis"]["worksheet_ids"][0]
    assert lab.client.post(f"/worksheets/weeks/{week_id}/cancel", headers=lab.headers["researcher_a"]).status_code == 403
    assert lab.client.post(f"/worksheets/{sheet_id}/cancel", headers=lab.headers["researcher_a"]).status_code == 403
    assert lab.client.post("/worksheets/weeks/nope/cancel", headers=lab.headers["leader"]).status_code == 404
    assert lab.client.get("/worksheets/next?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).status_code == 403
    assert lab.client.post(f"/worksheets/{sheet_id}/review", json={"comment": "Good."}, headers=lab.headers["leader"]).status_code == 200


def test_worksheets_made_before_weeks_become_week_one(lab):
    with closing(lab.db.shared()) as conn, conn:
        conn.execute("INSERT INTO worksheets (id, workspace_id, project_id, member_id, created_by, title, creao_run_id, created_at)"
                     " VALUES (?, 'demo-lab', 'heat-diffusion', 'researcher_a', 'leader', 'Old one', 'old-run', ?)",
                     (str(uuid.uuid4()), EARLIER))
    [old] = sheets(lab, "researcher_a")
    assert old["week"] == 1 and old["title"] == "Old one" and old["cancelled"] is None
    _, job = make(lab, [sheet("researcher_a", "New", lab.fb["id"])], only_new=False)
    assert job["diagnosis"]["week"] == 2
