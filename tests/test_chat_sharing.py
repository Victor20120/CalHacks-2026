"""Personal agents share what they establish in chat; members add detail and guesses.

The group chose this over approval for chat (2026-10-24). A chat share is always a
hypothesis, marked as shared by the agent, and its notes reach every agent.
"""
import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings
from backend.db.database import Database
from backend.orchestration.jobs import JobStore


def share_block(**share):
    return "\n\n```json\n" + json.dumps({"share": share or None}) + "\n```"


FINDING = dict(
    title="dt above 0.25 dx^2 makes the heat solver blow up",
    summary="With dx = 0.01 the explicit solver diverged at dt = 4e-5 and was stable at dt = 2e-5.",
    category="error_diagnosis",
    evidence="The user's two runs in this conversation.",
    agent_guess="Probably the explicit scheme's stability limit, not a bug in the boundary code.",
)


@pytest.fixture
def api(tmp_path):
    calls, jobs = [], JobStore()
    fake = SimpleNamespace(reply="Hello.")

    def run(inputs):
        calls.append(inputs)
        return SimpleNamespace(reply=fake.reply, run_id="chat-share-test")

    fake.run = run
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda slot: fake, data_dir=data, jobs=jobs,
                                   chat_setup=lambda: ChatSettings("CHAT", "test-fast-model")))
    headers = {}
    for member in ("researcher_a", "researcher_b", "leader"):
        token = client.post("/sign-in", json={"member_id": member}).json()["token"]
        headers[member] = {"Authorization": f"Bearer {token}"}
    return SimpleNamespace(client=client, calls=calls, fake=fake, headers=headers, db=Database(data_dir=data))


def chat(api, reply, message="It blew up at dt = 4e-5 but not 2e-5.", member="researcher_a", project="heat-diffusion"):
    api.fake.reply = reply
    job_id = api.client.post("/personal-agent/chat", json={"project_id": project, "message": message},
                             headers=api.headers[member]).json()["job_id"]
    return api.client.get(f"/jobs/{job_id}", headers=api.headers[member]).json()


def findings(api, member="researcher_a", project="heat-diffusion"):
    return api.client.get(f"/superbrain/findings?project_id={project}", headers=api.headers[member]).json()


def test_a_share_block_becomes_a_hypothesis_finding_without_a_draft(api):
    job = chat(api, "That is the CFL limit for this scheme." + share_block(**FINDING))
    assert job["status"] == "completed"
    reply = job["diagnosis"]["reply"]
    assert "```" not in reply and '"share"' not in reply
    assert reply.startswith("That is the CFL limit for this scheme.")
    assert reply.endswith(f'\n\nShared with your group in the Super Brain as a hypothesis: "{FINDING["title"]}".')
    assert job["diagnosis"]["shared_finding"]["title"] == FINDING["title"]
    assert api.client.get("/drafts", headers=api.headers["researcher_a"]).json() == []

    [finding] = findings(api, member="researcher_b")  # the group sees it at once
    assert finding["auto_shared"] is True
    assert finding["verification_status"] == "hypothesis"
    assert finding["category"] == "error_diagnosis"
    assert finding["contributed_by"] == "researcher_a"
    assert finding["source_creao_run_id"] == "chat-share-test"
    assert finding["agent_guess"] == FINDING["agent_guess"]
    assert "Evidence: The user's two runs" in finding["summary"]
    assert "not yet reviewed by the researcher" in finding["summary"]
    history = api.client.get("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).json()
    assert history["exchanges"][-1]["shared_finding"]["id"] == finding["id"]


def test_the_agent_is_told_when_and_how_to_share(api):
    chat(api, "Hello.")
    prompt = api.calls[-1]["observed_symptom"]
    assert '"share": null' in prompt and "never invent results" in prompt
    assert "Nothing here is shared automatically" not in prompt


@pytest.mark.parametrize("reply", [
    "Hi, I'm Agent 1.",
    "Hi." + share_block(),
    'Hi.\n\n```json\n{"share": "yes"}\n```',
    'Hi.\n\n```json\n{"share": {"title": "", "summary": "x"}}\n```',
    'Hi.\n\n```json\n{"share": {"title": "x"}}\n```',
    'Hi.\n\n```json\n{"share": {"title": "x", \n```',
])
def test_nothing_is_shared_without_a_usable_share_block(api, reply):
    job = chat(api, reply)
    assert job["status"] == "completed" and job["diagnosis"]["shared_finding"] is None
    assert "Shared with your group" not in job["diagnosis"]["reply"]
    assert findings(api) == []


def test_an_agent_cannot_mark_its_share_verified_or_pick_an_unknown_category(api):
    chat(api, "Done." + share_block(**{**FINDING, "verification_status": "verified", "category": "gossip"}))
    [finding] = findings(api)
    assert finding["verification_status"] == "hypothesis"
    assert finding["category"] == "research_result"


def test_the_same_title_is_not_shared_twice(api):
    chat(api, "Done." + share_block(**FINDING))
    again = chat(api, "Still the CFL limit." + share_block(**{**FINDING, "title": FINDING["title"].upper()}))
    assert again["diagnosis"]["shared_finding"] is None
    assert "Shared with your group" not in again["diagnosis"]["reply"]
    assert len(findings(api)) == 1
    chat(api, "B saw it too." + share_block(**FINDING), member="researcher_b")
    assert len(findings(api)) == 2  # another researcher's agent may share the same result


def test_secrets_are_removed_before_sharing(api):
    chat(api, "Done." + share_block(**{**FINDING, "summary": "Diverged. password = DO_NOT_SHARE_THIS",
                                       "agent_guess": "api_key = ALSO_SECRET"}))
    [finding] = findings(api)
    assert "DO_NOT_SHARE_THIS" not in finding["summary"]
    assert "ALSO_SECRET" not in finding["agent_guess"]


def test_shares_stay_in_their_project(api):
    chat(api, "Done." + share_block(**FINDING), project="wave-propagation")
    assert [f["project_id"] for f in findings(api, project="wave-propagation")] == ["wave-propagation"]
    assert api.client.get("/superbrain/findings?project_id=wave-propagation",
                          headers=api.headers["researcher_b"]).status_code in (403, 404)


def test_members_add_detail_guesses_and_corrections(api):
    chat(api, "Done." + share_block(**FINDING))
    [finding] = findings(api)
    path = f"/findings/{finding['id']}/notes"
    detail = api.client.post(path, json={"kind": "detail", "text": "Grid was 100 x 100."}, headers=api.headers["researcher_a"])
    assert detail.status_code == 201
    assert detail.json()["member_name"] and detail.json()["kind"] == "detail"
    guess = api.client.post(path, json={"kind": "guess", "text": "Could also be the corner nodes."},
                            headers=api.headers["researcher_b"])
    assert guess.status_code == 201
    api.client.post(path, json={"kind": "correction", "text": "dt was 4e-5, token = NOT_THIS"}, headers=api.headers["leader"])

    notes = api.client.get(f"/findings/{finding['id']}", headers=api.headers["researcher_b"]).json()["notes"]
    assert [n["kind"] for n in notes] == ["detail", "guess", "correction"]
    assert [n["member_id"] for n in notes] == ["researcher_a", "researcher_b", "leader"]
    assert "NOT_THIS" not in notes[2]["text"]

    assert api.client.post(path, json={"kind": "opinion", "text": "x"}, headers=api.headers["researcher_a"]).status_code == 422
    assert api.client.post(path, json={"kind": "detail", "text": ""}, headers=api.headers["researcher_a"]).status_code == 422
    assert api.client.post(path, json={"kind": "detail", "text": "x" * 2001}, headers=api.headers["researcher_a"]).status_code == 422
    assert api.client.post("/findings/nope/notes", json={"kind": "detail", "text": "x"},
                           headers=api.headers["researcher_a"]).status_code == 404
    assert api.client.post(path, json={"kind": "detail", "text": "x"}).status_code == 401


def test_only_members_of_the_findings_project_can_add_notes(api):
    chat(api, "Done." + share_block(**FINDING), project="wave-propagation")
    [finding] = findings(api, project="wave-propagation")
    response = api.client.post(f"/findings/{finding['id']}/notes", json={"kind": "guess", "text": "Mine."},
                               headers=api.headers["researcher_b"])
    assert response.status_code == 404  # as if it didn't exist
    with closing(api.db.shared()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM finding_notes").fetchone()[0] == 0


def test_the_super_brain_and_other_agents_see_the_guess_and_the_notes(api):
    chat(api, "Done." + share_block(**FINDING))
    [finding] = findings(api)
    api.client.post(f"/findings/{finding['id']}/notes", json={"kind": "correction", "text": "It was the 200 x 200 grid."},
                    headers=api.headers["researcher_b"])

    job = api.client.post("/superbrain/chat", json={"project_id": "heat-diffusion", "message": "Why did the heat solver blow up?"},
                          headers=api.headers["leader"]).json()
    context = api.calls[-1]["context"]
    assert finding["id"] in context
    assert "It was the 200 x 200 grid." in context and '"kind":"correction"' in context
    assert FINDING["agent_guess"] in context
    assert "personal agent, from a chat" in context
    assert job["prior_findings"][0]["id"] == finding["id"]

    chat(api, "Hello.", message="Why did the heat solver blow up?", member="researcher_b")
    assert "It was the 200 x 200 grid." in api.calls[-1]["observed_symptom"]
