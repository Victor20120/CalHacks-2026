"""Personal agent chat is private, project scoped, and never executes proposals."""
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings, CreaoTimeout
from backend.db.database import Database
from backend.memory.private_memory import PrivateMemory
from backend.orchestration.jobs import JobStore
from backend.orchestration.personal_chat import clean_personal_reply
from backend.workspaces.store import personal_agent_for_member


@pytest.fixture
def api(tmp_path):
    calls, slots, jobs = [], [], JobStore()
    fake = SimpleNamespace(error=None, reply="Build with your existing Makefile, then run the executable after a successful build.")
    def run(inputs):
        calls.append(inputs)
        if fake.error:
            raise fake.error
        return SimpleNamespace(reply=fake.reply, run_id="personal-test")
    fake.run = run
    def factory(slot):
        slots.append(slot)
        return fake
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=factory, data_dir=data, jobs=jobs,
                                  chat_setup=lambda: ChatSettings("CHAT", "test-fast-model")))
    headers = {}
    for member in ("researcher_a", "researcher_b", "leader"):
        auth = client.post("/sign-in", json={"member_id": member}).json()
        headers[member] = {"Authorization": f"Bearer {auth['token']}"}
    return SimpleNamespace(client=client, calls=calls, slots=slots, jobs=jobs, fake=fake,
                           headers=headers, db=Database(data_dir=data))


def ask(api, message="How do I compile and run this Fortran project?", member="researcher_a", project="heat-diffusion"):
    return api.client.post("/personal-agent/chat", json={"project_id": project, "message": message}, headers=api.headers[member])


def test_researchers_use_their_own_creao_agents_and_saved_skills(api):
    first = ask(api)
    assert first.status_code == 202
    second = ask(api, member="researcher_b")
    assert second.status_code == 202
    assert api.slots == ["AGENT_1", "AGENT_2"]
    assert "configured CREAO skills and tools" in api.calls[0]["observed_symptom"]
    assert "compilation and linking before execution" in api.calls[0]["observed_symptom"]
    assert api.calls[0]["analysis_depth"] == "standard"
    assert "chatModelId" not in api.calls[0]
    job = api.client.get(f"/jobs/{first.json()['job_id']}", headers=api.headers["researcher_a"]).json()
    assert job["kind"] == "personal_chat" and job["status"] == "completed"
    assert job["diagnosis"]["agent_slot"] == "AGENT_1"
    assert job["draft_id"] is None


def test_agent_identity_is_returned_saved_and_used_for_its_introduction(api):
    a = api.client.get("/me", headers=api.headers["researcher_a"]).json()["agent"]
    b = api.client.get("/me", headers=api.headers["researcher_b"]).json()["agent"]
    assert a == {"id": "agent-researcher_a", "number": 1, "name": "Agent 1"}
    assert b == {"id": "agent-researcher_b", "number": 2, "name": "Agent 2"}
    assert api.client.get("/me", headers=api.headers["leader"]).json()["agent"] is None
    assert api.calls == []  # identity does not require a provider run
    first = ask(api, "Who are you?")
    assert first.json()["agent"] == a
    assert "You are Agent 1" in api.calls[-1]["observed_symptom"]
    assert "introduce yourself as Agent 1" in api.calls[-1]["observed_symptom"]
    history = api.client.get("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).json()
    assert history["agent"] == a
    assert history["exchanges"][0]["agent"] == a
    ask(api, "And which compiler?")
    assert "introduce yourself as Agent 1" not in api.calls[-1]["observed_symptom"]
    assert "You are Agent 1" in api.calls[-1]["observed_symptom"]
    ask(api, "Who are you?", member="researcher_b")
    assert "You are Agent 2" in api.calls[-1]["observed_symptom"]
    assert "introduce yourself as Agent 2" in api.calls[-1]["observed_symptom"]


def test_numbers_follow_activation_order_not_names_slots_or_active_agent_count(api):
    with closing(api.db.shared()) as conn:
        # An independent workspace: reverse alphabetical order, arbitrary CREAO
        # slots, and identical timestamps must preserve insertion/activation order.
        conn.execute("INSERT INTO workspaces VALUES ('other', 'Other group', '2026-10-24')")
        for member, slot in [("zeta", "AGENT_99"), ("alpha", "AGENT_8")]:
            conn.execute("INSERT INTO members (id, workspace_id, display_name, role, created_at) "
                         "VALUES (?, 'other', ?, 'researcher', '2026-10-24')", (member, member))
            conn.execute("INSERT INTO agents (id, member_id, creao_env_prefix, created_at) "
                         "VALUES (?, ?, ?, '2026-10-24')", (f"agent-{member}", member, slot))
        assert personal_agent_for_member(conn, "zeta")["name"] == "Agent 1"
        assert personal_agent_for_member(conn, "alpha")["name"] == "Agent 2"
        conn.execute("UPDATE agents SET status='inactive' WHERE member_id='zeta'")
        assert personal_agent_for_member(conn, "zeta") is None
        assert personal_agent_for_member(conn, "alpha")["name"] == "Agent 2"
        conn.execute("INSERT INTO members (id, workspace_id, display_name, role, created_at) "
                     "VALUES ('newcomer', 'other', 'Newcomer', 'researcher', '2026-10-25')")
        conn.execute("INSERT INTO agents (id, member_id, creao_env_prefix, created_at) "
                     "VALUES ('agent-newcomer', 'newcomer', 'AGENT_1', '2026-10-25')")
        assert personal_agent_for_member(conn, "newcomer")["name"] == "Agent 3"
        assert personal_agent_for_member(conn, "researcher_a")["name"] == "Agent 1"
        conn.commit()
    # Reopening storage, switching projects, and clearing chat do not allocate
    # new identities or depend on which chat was opened first.
    with closing(Database(data_dir=api.db.data_dir).shared()) as conn:
        assert personal_agent_for_member(conn, "alpha")["name"] == "Agent 2"
    ask(api, project="wave-propagation")
    assert api.calls[-1]["observed_symptom"].count('"agent_name":"Agent 1"') == 1
    api.client.delete("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"])
    assert api.client.get("/me", headers=api.headers["researcher_a"]).json()["agent"]["name"] == "Agent 1"


def test_personal_overview_and_conversation_never_enter_other_members_or_hub_chat(api):
    a = PrivateMemory(api.db, "researcher_a")
    a.save_project_context("heat-diffusion", None, "PRIVATE_FORTRAN_OVERVIEW_MARKER", {}, "overview-test")
    first = ask(api, "PRIVATE_QUESTION_MARKER: which compiler should I use?")
    assert first.json()["used_project_overview"] is True
    assert "PRIVATE_FORTRAN_OVERVIEW_MARKER" in api.calls[-1]["observed_symptom"]
    ask(api, "And which build target?")
    assert "PRIVATE_QUESTION_MARKER" in api.calls[-1]["observed_symptom"]
    ask(api, member="researcher_b")
    assert "PRIVATE_FORTRAN_OVERVIEW_MARKER" not in api.calls[-1]["observed_symptom"]
    assert "PRIVATE_QUESTION_MARKER" not in api.calls[-1]["observed_symptom"]
    hub = api.client.post("/superbrain/chat", json={"project_id": "heat-diffusion", "message": "Explain compilation"}, headers=api.headers["researcher_a"])
    assert hub.status_code == 202 and api.slots[-1] == "CHAT"
    assert "PRIVATE_FORTRAN_OVERVIEW_MARKER" not in api.calls[-1]["context"]
    assert "PRIVATE_QUESTION_MARKER" not in api.calls[-1]["context"]
    assert len(a.chat_history("heat-diffusion", "personal")) == 2
    assert len(a.chat_history("heat-diffusion")) == 1
    assert api.client.get(f"/jobs/{first.json()['job_id']}", headers=api.headers["researcher_b"]).status_code == 404
    assert b"PRIVATE_QUESTION_MARKER" not in api.db.shared_path.read_bytes()


def test_rules_can_be_refined_in_chat_and_survive_clearing(api):
    response = ask(api, "Act as a researcher and explain compiler flags clearly.")
    assert response.status_code == 202
    assert "compiler flags clearly" in response.json()["instructions"]
    assert "compiler flags clearly" in api.calls[-1]["observed_symptom"]
    memory = PrivateMemory(api.db, "researcher_a")
    assert "compiler flags clearly" in memory.agent_rules("heat-diffusion")
    assert "compiler flags clearly" not in PrivateMemory(api.db, "researcher_b").agent_rules("heat-diffusion")
    ask(api, "A private wave conversation", project="wave-propagation")
    assert api.client.delete("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).status_code == 200
    assert memory.chat_history("heat-diffusion", "personal") == []
    assert memory.chat_history("wave-propagation", "personal")
    assert "compiler flags clearly" in memory.agent_rules("heat-diffusion")


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/projects/wave-propagation/personal-chat", None),
    ("DELETE", "/projects/wave-propagation/personal-chat", None),
    ("POST", "/personal-agent/chat", {"project_id": "wave-propagation", "message": "Explain this"}),
])
def test_personal_chat_requires_authentication_and_project_membership(api, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert api.client.request(method, path, **kwargs).status_code == 401
    assert api.client.request(method, path, headers=api.headers["researcher_b"], **kwargs).status_code == 403
    assert api.calls == []


def test_leader_cannot_impersonate_a_personal_agent_and_offboarding_revokes_access(api):
    assert ask(api, member="leader").status_code == 403
    with closing(api.db.shared()) as conn:
        conn.execute("UPDATE members SET status='offboarded' WHERE id='researcher_a'")
        conn.commit()
    assert ask(api).status_code == 403
    assert api.client.get("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).status_code == 403
    assert api.calls == []


def test_pending_personal_replies_reconnect_and_do_not_mix_with_hub_jobs(api):
    job = api.jobs.create("researcher_a", "heat-diffusion", kind="personal_chat", scope="personal")
    result = api.client.get("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).json()
    assert result["pending_job_id"] == job.job_id
    assert ask(api).status_code == 409
    assert api.client.delete("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).status_code == 409
    assert api.client.get("/projects/heat-diffusion/chat", headers=api.headers["researcher_a"]).json()["pending_job_id"] is None
    assert api.calls == []


def test_failures_are_shown_without_saving_a_fabricated_reply(api):
    api.fake.error = CreaoTimeout("test-timeout", 1)
    response = ask(api)
    job = api.client.get(f"/jobs/{response.json()['job_id']}", headers=api.headers["researcher_a"]).json()
    assert job["status"] == "failed" and job["error"]["type"] == "timeout"
    assert api.client.get("/projects/heat-diffusion/personal-chat", headers=api.headers["researcher_a"]).json()["exchanges"] == []


def test_chat_redacts_secrets_validates_inputs_and_never_executes_or_publishes(api):
    assert ask(api, " ").status_code == 422
    assert ask(api, "x" * 4001).status_code == 422
    assert api.client.post("/personal-agent/chat", json={"project_id": "heat-diffusion", "message": "hi", "agent_slot": "AGENT_2"}, headers=api.headers["researcher_a"]).status_code == 422
    response = ask(api, "Compile the solver. password = DO_NOT_SEND_THIS")
    assert response.status_code == 202
    assert "DO_NOT_SEND_THIS" not in api.calls[-1]["observed_symptom"]
    assert api.client.get("/drafts", headers=api.headers["researcher_a"]).json() == []
    with closing(api.db.shared()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shared_findings").fetchone()[0] == 0
    assert "this chat does not execute them or edit project files" in api.calls[-1]["observed_symptom"]


@pytest.mark.parametrize("reply,expected", [
    ("I'll start by reading the agent's skill manifest and SKILL.md.## What numerical instability is\nErrors grow.",
     "## What numerical instability is\nErrors grow."),
    ("I’ll first check SKILL.md.\nSmall errors grow instead of shrinking.", "Small errors grow instead of shrinking."),
    ("I'll start by reading the agent's SKILL.md and manifest.I'm Agent 1. Let's discuss your solver.",
     "I'm Agent 1. Let's discuss your solver."),
    ("I'll start by reading the agent's skill and manifest.Your bracket is good evidence.",
     "Your bracket is good evidence."),
    ("Numerical instability means small errors grow over time.", "Numerical instability means small errors grow over time."),
    ("I can't read your local SKILL.md file. Please share the relevant text.",
     "I can't read your local SKILL.md file. Please share the relevant text."),
    ("I'll review SKILL.md, but cannot access your local files. Please share it.",
     "I'll review SKILL.md, but cannot access your local files. Please share it."),
    ("SKILL.md describes the agent's workflow. You can edit it in CREAO.",
     "SKILL.md describes the agent's workflow. You can edit it in CREAO."),
    ("I'll review your simulation manifest. It defines the allowed run command.",
     "I'll review your simulation manifest. It defines the allowed run command."),
    ("> I'll read SKILL.md.\nThat is the sentence you asked about.",
     "> I'll read SKILL.md.\nThat is the sentence you asked about."),
    ("I'll read SKILL.md.", "I'll read SKILL.md."),
])
def test_only_leaked_skill_preparation_is_removed(reply, expected):
    assert clean_personal_reply(reply) == expected


def test_personal_chat_saves_and_reuses_the_answer_without_skill_preamble(api):
    api.fake.reply = "I'll start by reading the agent's skill manifest and SKILL.md.Small errors grow instead of shrinking."
    first = ask(api, "What is numerical instability?")
    job = api.client.get(f"/jobs/{first.json()['job_id']}", headers=api.headers["researcher_a"]).json()
    assert job["diagnosis"]["reply"] == "Small errors grow instead of shrinking."
    memory = PrivateMemory(api.db, "researcher_a")
    assert memory.chat_history("heat-diffusion", "personal")[0]["reply"] == "Small errors grow instead of shrinking."
    # Historical replies may predate the fix. Avoid feeding their leaked setup
    # narration back as a conversational example on subsequent requests.
    memory.save_chat_exchange("heat-diffusion", "Earlier question", {"reply": api.fake.reply}, "personal")
    ask(api, "Can you give a simple example?")
    prompt = api.calls[-1]["observed_symptom"]
    assert "I'll start by reading the agent's skill manifest" not in prompt
    assert "This is a conversation, not an automatic audit" in prompt
    assert "two to five sentences" in prompt
    assert "configured CREAO skills and tools" in prompt
    assert "Small errors grow instead of shrinking." in prompt


def test_conversational_style_can_be_saved_by_talking_to_the_agent(api):
    response = ask(api, "Be conversational and answer directly. Keep explanations short unless I ask for detail.")
    assert "Be conversational" in response.json()["instructions"]
    ask(api, "What is numerical instability?")
    assert "Be conversational and answer directly." in api.calls[-1]["observed_symptom"]
    assert "Be conversational" in PrivateMemory(api.db, "researcher_a").agent_rules("heat-diffusion")
    assert "Be conversational" not in PrivateMemory(api.db, "researcher_b").agent_rules("heat-diffusion")
