"""Chat, instructions, and project isolation. CREAO is mocked; no credits used."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings, CreaoTimeout, chat_settings
from backend.creao.client import FAST_CHAT_MODEL_ID
from backend.db.database import Database
from backend.memory.private_memory import PrivateMemory
from backend.orchestration.chat import DEFAULT_AGENT_RULES, MAX_CHAT_EXCHANGES, rule_command, run_chat
from backend.orchestration.jobs import JobStore


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.error = None
        self.result = None

    def run(self, inputs):
        self.calls.append(inputs)
        if self.error:
            raise self.error
        return self.result or SimpleNamespace(reply="Check the time step against the grid spacing.", run_id="mock-chat",
                               summary={}, issues=[], analysis_ran=False)


@pytest.fixture
def api(tmp_path):
    fake, slots, jobs = FakeAgent(), [], JobStore()
    def factory(prefix):
        slots.append(prefix)
        return fake
    path = tmp_path / "data"
    client = TestClient(create_app(client_factory=factory, data_dir=path, jobs=jobs))
    headers = {}
    for member in ("researcher_a", "researcher_b", "leader"):
        token = client.post("/sign-in", json={"member_id": member}).json()["token"]
        headers[member] = {"Authorization": f"Bearer {token}"}
    memory = PrivateMemory(Database(data_dir=path), "researcher_a")
    draft = memory.add_draft("heat-diffusion", {"title": "Time stepping research", "summary":
        "The recorded integration interval must respect the spatial mesh constraint.",
        "category": "research_result", "verification_status": "hypothesis"})
    assert client.post(f"/drafts/{draft}/approve", json={}, headers=headers["researcher_a"]).status_code == 200
    return SimpleNamespace(client=client, fake=fake, slots=slots, jobs=jobs, path=path, headers=headers)


def post_chat(api, message="Why is the solver unstable?", member="researcher_a", project="heat-diffusion", **options):
    return api.client.post("/agent/chat", json={"project_id": project, "message": message, **options}, headers=api.headers[member])


def test_chat_replies_and_followups_use_server_history(api):
    response = post_chat(api)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = api.client.get(f"/jobs/{job_id}", headers=api.headers["researcher_a"]).json()
    assert job["status"] == "completed" and job["kind"] == "chat"
    assert "grid spacing" in job["diagnosis"]["reply"]
    assert api.slots == ["HUB"]
    assert DEFAULT_AGENT_RULES in api.fake.calls[0]["observed_symptom"]
    post_chat(api, "How should I change it?")
    assert "Why is the solver unstable?" in api.fake.calls[-1]["observed_symptom"]
    assert "Check the time step" in api.fake.calls[-1]["observed_symptom"]
    history = api.client.get("/projects/heat-diffusion/chat", headers=api.headers["researcher_a"]).json()
    assert len(history["exchanges"]) == 2 and history["pending_job_id"] is None
    assert api.client.get("/projects/heat-diffusion/chat", headers=api.headers["researcher_b"]).json()["exchanges"] == []
    assert api.client.get(f"/jobs/{job_id}", headers=api.headers["researcher_b"]).status_code == 404
    assert api.client.get("/drafts", headers=api.headers["researcher_a"]).json() == []


def test_all_super_brain_conversations_use_the_central_hub(api):
    assert post_chat(api, member="leader").status_code == 202
    assert post_chat(api, member="researcher_b").status_code == 202
    assert api.slots == ["HUB", "HUB"]


def test_instructions_are_private_persistent_and_project_scoped(api):
    endpoint = "/projects/heat-diffusion/agent-rules"
    rules = "Use formal research language. CUSTOM_PRIVATE_RULE_MARKER"
    assert api.client.put(endpoint, json={"instructions": rules}, headers=api.headers["researcher_a"]).status_code == 200
    post_chat(api)
    assert rules in api.fake.calls[-1]["observed_symptom"]
    assert api.client.get(endpoint, headers=api.headers["researcher_b"]).json()["instructions"] == DEFAULT_AGENT_RULES
    assert api.client.get("/projects/wave-propagation/agent-rules", headers=api.headers["researcher_a"]).json()["instructions"] == DEFAULT_AGENT_RULES
    # Opening a fresh app with the same files retains settings and conversation.
    client = TestClient(create_app(data_dir=api.path))
    token = client.post("/sign-in", json={"member_id": "researcher_a"}).json()["token"]
    assert client.get(endpoint, headers={"Authorization": f"Bearer {token}"}).json()["instructions"] == rules
    assert b"CUSTOM_PRIVATE_RULE_MARKER" not in (api.path / "superbrain.db").read_bytes()
    assert api.client.get("/settings", headers=api.headers["researcher_a"]).json() == {"save_alert_history": False}


@pytest.mark.parametrize("method,path,body", [
    ("get", "/projects/wave-propagation/chat", None),
    ("delete", "/projects/wave-propagation/chat", None),
    ("get", "/projects/wave-propagation/agent-rules", None),
    ("put", "/projects/wave-propagation/agent-rules", {"instructions": "Do research"}),
    ("post", "/agent/chat", {"project_id": "wave-propagation", "message": "Explain this project"}),
])
def test_unauthorized_project_never_reaches_creao(api, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert api.client.request(method, path, headers=api.headers["researcher_b"], **kwargs).status_code == 403
    assert api.client.request(method, path, **kwargs).status_code == 401
    assert api.fake.calls == []


def test_validation_redaction_and_reset(api):
    h = api.headers["researcher_a"]
    path = "/projects/heat-diffusion/agent-rules"
    assert api.client.put(path, json={"instructions": "x" * 2001}, headers=h).status_code == 422
    assert post_chat(api, " ").status_code == 422
    assert post_chat(api, "x" * 4001).status_code == 422
    result = api.client.put(path, json={"instructions": "Stay professional. password = hunter2"}, headers=h).json()
    assert "hunter2" not in result["instructions"]
    post_chat(api, "Check this config: password = hunter2")
    assert "hunter2" not in api.fake.calls[-1]["observed_symptom"]
    assert api.client.put(path, json={"instructions": " "}, headers=h).json()["instructions"] == DEFAULT_AGENT_RULES


def test_failures_are_visible_and_not_saved_as_replies(api):
    api.fake.error = CreaoTimeout("mock-timeout", 10)
    job_id = post_chat(api).json()["job_id"]
    result = api.client.get(f"/jobs/{job_id}", headers=api.headers["researcher_a"]).json()
    assert result["status"] == "failed" and result["error"]["type"] == "timeout"
    assert api.client.get("/projects/heat-diffusion/chat", headers=api.headers["researcher_a"]).json()["exchanges"] == []


def test_running_job_can_be_reconnected_and_cannot_be_duplicated_or_cleared(api):
    job = api.jobs.create("researcher_a", "heat-diffusion", kind="chat")
    h = api.headers["researcher_a"]
    assert api.client.get("/projects/heat-diffusion/chat", headers=h).json()["pending_job_id"] == job.job_id
    assert post_chat(api).status_code == 409
    assert api.client.delete("/projects/heat-diffusion/chat", headers=h).status_code == 409
    assert api.fake.calls == []


def test_clear_keeps_rules_and_other_projects_and_history_is_bounded(api):
    h = api.headers["researcher_a"]
    api.client.put("/projects/heat-diffusion/agent-rules", json={"instructions": "Be concise"}, headers=h)
    memory = PrivateMemory(Database(data_dir=api.path), "researcher_a")
    for n in range(MAX_CHAT_EXCHANGES + 3):
        memory.save_chat_exchange("heat-diffusion", f"question {n}", {"reply": "answer", "run_id": "fake"})
    history = memory.chat_history("heat-diffusion")
    assert len(history) == MAX_CHAT_EXCHANGES and history[0]["question"] == "question 3"
    memory.save_chat_exchange("wave-propagation", "PRIVATE_WAVE_CONTEXT", {"reply": "wave", "run_id": "fake"})
    post_chat(api)
    assert "PRIVATE_WAVE_CONTEXT" not in api.fake.calls[-1]["observed_symptom"]
    assert api.client.delete("/projects/heat-diffusion/chat", headers=h).status_code == 200
    assert memory.chat_history("heat-diffusion") == []
    assert memory.chat_history("wave-propagation")
    assert memory.agent_rules("heat-diffusion") == "Be concise"


def test_saved_rules_apply_to_diagnoses_and_overviews(api):
    h = api.headers["researcher_a"]
    rules = "Explain scientific assumptions in formal language."
    api.client.put("/projects/heat-diffusion/agent-rules", json={"instructions": rules}, headers=h)
    assert api.client.post("/diagnose", json={"project_id": "heat-diffusion", "symptom": "unstable"}, headers=h).status_code == 202
    assert rules in api.fake.calls[-1]["observed_symptom"]
    assert api.client.post("/overview", json={"project_id": "heat-diffusion", "tree": "research/  (0 files)"}, headers=h).status_code == 202
    assert rules in api.fake.calls[-1]["observed_symptom"]


def test_chat_context_contains_only_approved_findings_in_selected_project(api):
    memory = PrivateMemory(Database(data_dir=api.path), "researcher_a")
    for project, marker, approve in [("heat-diffusion", "HEAT_APPROVED_MARKER", True),
                                     ("heat-diffusion", "PRIVATE_DRAFT_MARKER", False),
                                     ("wave-propagation", "RESTRICTED_WAVE_MARKER", True)]:
        draft = memory.add_draft(project, {"title": "stability limit", "summary": marker,
                                          "category": "research_result", "verification_status": "hypothesis"})
        if approve:
            assert api.client.post(f"/drafts/{draft}/approve", json={}, headers=api.headers["researcher_a"]).status_code == 200
    post_chat(api, "stability limit", member="researcher_b")
    sent = api.fake.calls[-1]["observed_symptom"]
    assert "HEAT_APPROVED_MARKER" in sent
    assert "PRIVATE_DRAFT_MARKER" not in sent and "RESTRICTED_WAVE_MARKER" not in sent
    history = api.client.get("/projects/heat-diffusion/chat", headers=api.headers["researcher_b"]).json()
    assert len(history["exchanges"][0]["sources"]) == 1


def test_greetings_and_rules_are_answered_by_creao_hub(api):
    hello = post_chat(api, "hello")
    assert hello.status_code == 202 and hello.json()["agent_slot"] == "HUB"
    update = post_chat(api, "Act as a researcher, be clear and concise.")
    assert update.status_code == 202
    rules = update.json()["instructions"]
    assert "be clear and concise" in rules
    assert api.slots == ["HUB", "HUB"]
    assert rules in api.fake.calls[-1]["observed_symptom"]
    sent = api.fake.calls[-1]["observed_symptom"]
    assert '"instruction_action_completed_by_app":"append"' in sent
    assert api.client.get("/drafts", headers=api.headers["researcher_a"]).json() == []


def test_rules_commands_are_saved_before_hub_generates_its_reply(api):
    assert post_chat(api, "/rules Be concise and cite shared evidence.").json()["instructions"] == "Be concise and cite shared evidence."
    assert post_chat(api, "/rules show").status_code == 202
    sent = api.fake.calls[-1]["observed_symptom"]
    assert "Be concise and cite shared evidence." in sent
    assert '"instruction_action_completed_by_app":"show"' in sent
    assert post_chat(api, "/rules reset").json()["instructions"] == DEFAULT_AGENT_RULES
    assert post_chat(api, "/rules " + "x" * 2001).status_code == 422
    assert api.slots == ["HUB", "HUB", "HUB"]


@pytest.mark.parametrize("message", ["Act as a researcher: why is dt unstable?",
    "Keep the boundary conditions fixed and explain why", "How should a researcher be clear?",
    "The log says: act as a researcher", "Use a professional solver: which one is suitable?"])
def test_questions_and_quoted_style_text_are_not_saved_as_rules(message):
    assert rule_command(message) is None


def test_empty_specialized_knowledge_still_uses_creao_for_general_reasoning(api):
    empty = post_chat(api, "Explain the stability of an explicit heat solver.", project="wave-propagation")
    assert empty.status_code == 202 and empty.json()["prior_findings"] == []
    assert api.slots == ["HUB"]
    sent = api.fake.calls[-1]["observed_symptom"]
    assert "general scientific knowledge and reasoning" in sent
    assert '"selected_findings":[]' in sent
    assert "instead of inventing it" in sent
    # A local lookup cannot silently replace the CREAO conversation.
    assert post_chat(api, "Explain this", mode="lookup").status_code == 422


def test_broad_questions_include_multiple_researchers_even_without_keyword_matches(api):
    memory = PrivateMemory(Database(data_dir=api.path), "researcher_b")
    draft = memory.add_draft("heat-diffusion", {"title": "Boundary rows", "summary": "BOUNDARY_MARKER",
        "category": "research_result", "verification_status": "hypothesis"})
    api.client.post(f"/drafts/{draft}/approve", json={}, headers=api.headers["researcher_b"])
    response = post_chat(api, "What have we learned as a team?")
    assert response.status_code == 202
    assert {x["contributed_by_name"] for x in response.json()["prior_findings"]} == {"Researcher A", "Researcher B"}
    assert response.json()["knowledge_stats"]["researchers"] == 2
    sent = api.fake.calls[-1]["observed_symptom"]
    assert "BOUNDARY_MARKER" in sent and "spatial mesh constraint" in sent
    assert "central CREAO Super Brain agent" in sent


def test_all_project_scope_only_uses_accessible_knowledge_and_separates_history(api):
    memory = PrivateMemory(Database(data_dir=api.path), "researcher_a")
    draft = memory.add_draft("wave-propagation", {"title": "Wave result", "summary": "WAVE_ONLY_MARKER",
        "category": "research_result", "verification_status": "hypothesis"})
    api.client.post(f"/drafts/{draft}/approve", json={}, headers=api.headers["researcher_a"])
    a = post_chat(api, "Give an overview", scope="all")
    assert a.status_code == 202 and "WAVE_ONLY_MARKER" in api.fake.calls[-1]["observed_symptom"]
    assert a.json()["knowledge_stats"]["projects"] == 2
    assert api.client.get("/projects/heat-diffusion/chat", headers=api.headers["researcher_a"]).json()["exchanges"] == []
    b = post_chat(api, "Give an overview", member="researcher_b", scope="all")
    assert b.status_code == 202 and "WAVE_ONLY_MARKER" not in api.fake.calls[-1]["observed_symptom"]
    assert b.json()["knowledge_stats"]["projects"] == 1
    assert post_chat(api, "hello", scope="outside").status_code == 422


def test_losing_project_access_hides_old_combined_answers_and_jobs(api):
    response = post_chat(api, "Give an overview", scope="all")
    with Database(data_dir=api.path).shared() as conn:
        conn.execute("DELETE FROM project_members WHERE member_id = 'researcher_a' AND project_id = 'wave-propagation'")
    h = api.headers["researcher_a"]
    assert api.client.get("/projects/heat-diffusion/chat?scope=all", headers=h).json()["exchanges"] == []
    assert api.client.get(f"/jobs/{response.json()['job_id']}", headers=h).status_code == 404


def test_creao_prompt_history_and_replies_are_bounded_and_timed():
    fake = FakeAgent()
    history = [{"question": "q" * 4000, "reply": "r" * 12000} for _ in range(12)]
    result = run_chat(fake, "project", "Explain the results", DEFAULT_AGENT_RULES, history, {"selected_findings": [], "available_knowledge": {}})
    assert len(fake.calls[-1]["observed_symptom"]) < 7500
    assert result["provider_ms"] >= 0 and result["prompt_chars"] > 0


def test_chat_uses_shorter_polling_without_changing_diagnosis_client(api, monkeypatch):
    import importlib
    module = importlib.import_module("backend.api.app")
    intervals = []
    monkeypatch.setattr(module, "hub_agent", lambda: "hub-config")
    def client(config, **kwargs):
        intervals.append(kwargs.get("poll_interval", 5.0))
        return api.fake
    monkeypatch.setattr(module, "CreaoClient", client)
    fresh = TestClient(create_app(data_dir=api.path))
    token = fresh.post("/sign-in", json={"member_id": "leader"}).json()["token"]
    assert fresh.post("/agent/chat", json={"project_id": "heat-diffusion", "message": "Summarize our findings"},
                      headers={"Authorization": f"Bearer {token}"}).status_code == 202
    assert intervals == [1.0]


@pytest.mark.parametrize("model", [FAST_CHAT_MODEL_ID, None])
def test_chat_uses_the_chosen_model_and_diagnoses_keep_their_own(api, monkeypatch, model):
    import importlib
    module = importlib.import_module("backend.api.app")
    options = []
    monkeypatch.setattr(module, "hub_agent", lambda: "hub-config")
    monkeypatch.setattr(module, "personal_agent", lambda prefix: prefix)
    def client(config, **kwargs):
        options.append((config, kwargs))
        return api.fake
    monkeypatch.setattr(module, "CreaoClient", client)
    fresh = TestClient(create_app(data_dir=api.path, chat_setup=lambda: ChatSettings("HUB", model)))
    token = fresh.post("/sign-in", json={"member_id": "researcher_a"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert fresh.post("/superbrain/chat", json={"project_id": "heat-diffusion", "message": "Compare the results"}, headers=headers).status_code == 202
    assert options[0] == ("hub-config", {"poll_interval": 1.0, "chat_model_id": model})  # None: the agent's saved model
    assert fresh.post("/diagnose", json={"project_id": "heat-diffusion", "symptom": "unstable"}, headers=headers).status_code == 202
    assert options[1] == ("AGENT_1", {})


def test_hub_chat_asks_for_a_direct_answer_without_its_audit_workflow():
    fake = FakeAgent()
    run_chat(fake, "project", "Compare the findings", DEFAULT_AGENT_RULES, [],
             {"selected_findings": [], "available_knowledge": {}})
    sent = fake.calls[0]
    assert "Do not run an audit, plan, call tools" in sent["observed_symptom"]  # the workflow is the slow part
    assert "analysis_depth" not in sent


def test_personal_agent_diagnoses_reach_the_hub_after_researchers_share_them(api):
    for member, run, title, evidence in [
        ("researcher_a", "a-run", "Baseline heat stability", "A measured dx=0.01, alpha=0.001, stable dt=0.02. A_EVIDENCE_MARKER"),
        ("researcher_b", "b-run", "Refined heat stability", "B measured dx=0.005, alpha=0.001, unstable dt=0.01. B_EVIDENCE_MARKER"),
    ]:
        api.fake.result = SimpleNamespace(reply=evidence, run_id=run, summary={}, analysis_ran=True,
            issues=[{"title": title, "status": "suspected", "confidence": "high", "evidence": evidence,
                     "proposed_validation": "Compare alpha * dt / dx**2 with the two-dimensional bound of 0.25."}])
        response = api.client.post("/diagnose", json={"project_id": "heat-diffusion", "symptom": title}, headers=api.headers[member])
        assert response.status_code == 202
        job = api.client.get(f"/jobs/{response.json()['job_id']}", headers=api.headers[member]).json()
        assert job["draft_id"]
        assert api.client.post(f"/drafts/{job['draft_id']}/approve", json={}, headers=api.headers[member]).status_code == 200
    api.fake.result = None
    response = api.client.post("/superbrain/chat", json={"project_id": "heat-diffusion",
        "message": "Compare what Researcher A and Researcher B learned about heat stability."}, headers=api.headers["researcher_b"])
    assert response.status_code == 202
    assert api.slots == ["AGENT_1", "AGENT_2", "HUB"]
    sent = api.fake.calls[-1]["observed_symptom"]
    assert "A_EVIDENCE_MARKER" in sent and "B_EVIDENCE_MARKER" in sent
    assert "a-run" in sent and "b-run" in sent
    assert {s["contributed_by_name"] for s in response.json()["prior_findings"]} == {"Researcher A", "Researcher B"}
    job = api.client.get(f"/jobs/{response.json()['job_id']}", headers=api.headers["researcher_b"]).json()
    assert job["diagnosis"]["agent_slot"] == "HUB"


def test_hub_gets_full_shared_evidence_but_not_private_overviews(api):
    memory = PrivateMemory(Database(data_dir=api.path), "researcher_a")
    evidence = "Background. " * 90 + "IMPORTANT_SPECIALIZED_DETAIL_AT_THE_END"
    draft = memory.add_draft("heat-diffusion", {"title": "Long research record", "summary": evidence,
        "category": "research_result", "verification_status": "hypothesis"})
    api.client.post(f"/drafts/{draft}/approve", json={}, headers=api.headers["researcher_a"])
    memory.save_project_context("heat-diffusion", None, "PRIVATE_OVERVIEW_NEVER_SHARED", {}, "private-run")
    assert post_chat(api, "Explain the long research record").status_code == 202
    sent = api.fake.calls[-1]["observed_symptom"]
    assert "IMPORTANT_SPECIALIZED_DETAIL_AT_THE_END" in sent
    assert "PRIVATE_OVERVIEW_NEVER_SHARED" not in sent


# --- The dedicated chat agent and the fast model ----------------------------


def test_chat_settings_come_from_env(monkeypatch):
    for name in ("CREAO_CHAT_APP_ID", "CREAO_CHAT_KEY", "CREAO_CHAT_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    assert chat_settings(env_file=None) == ChatSettings("HUB", FAST_CHAT_MODEL_ID)  # hub until the chat agent exists

    monkeypatch.setenv("CREAO_CHAT_APP_ID", "app-chat")
    monkeypatch.setenv("CREAO_CHAT_KEY", "capi_FAKE_chat")
    monkeypatch.setenv("CREAO_CHAT_MODEL_ID", "saved")
    assert chat_settings(env_file=None) == ChatSettings("CHAT", None)  # "saved" keeps the agent's own model


def test_the_chat_agent_gets_only_a_question_and_its_context():
    fake = FakeAgent()
    findings = {"available_knowledge": {"findings": 1}, "selected_findings": [{"id": "f-1", "summary": "SHARED_MARKER"}]}
    run_chat(fake, "Heat diffusion solver", "Why does it blow up?", "Be brief.", [], findings, dedicated=True)

    [sent] = fake.calls
    assert set(sent) == {"question", "context"}
    assert sent["question"] == "Why does it blow up?"
    assert "Be brief." in sent["context"] and "SHARED_MARKER" in sent["context"] and "Heat diffusion solver" in sent["context"]


def test_chat_runs_on_the_dedicated_agent_once_it_is_set_up(tmp_path):
    fake, slots = FakeAgent(), []
    def factory(prefix):
        slots.append(prefix)
        return fake
    client = TestClient(create_app(client_factory=factory, data_dir=tmp_path / "data",
                                   chat_setup=lambda: ChatSettings("CHAT", FAST_CHAT_MODEL_ID)))
    token = client.post("/sign-in", json={"member_id": "leader"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    started = client.post("/superbrain/chat", json={"project_id": "heat-diffusion", "message": "What do we know?"}, headers=headers)
    job = client.get(f"/jobs/{started.json()['job_id']}", headers=headers).json()

    assert slots == ["CHAT"] and set(fake.calls[0]) == {"question", "context"}
    assert started.json()["agent_slot"] == "CHAT"
    assert (job["diagnosis"]["agent_slot"], job["diagnosis"]["model"]) == ("CHAT", FAST_CHAT_MODEL_ID)
