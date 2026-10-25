"""Tests for the CREAO adapter. CREAO is faked with saved real responses, so
these tests need no network, no API key and no credits."""
import json
import logging
from pathlib import Path

import pytest
import requests

from backend.creao import (
    AgentConfig,
    CreaoClient,
    CreaoConfigError,
    CreaoInputError,
    CreaoRequestError,
    CreaoRunFailed,
    CreaoTimeout,
    hub_agent,
    parse_run,
    personal_agent,
)
from backend.creao.client import HUB_REQUIRED_INPUTS, ISSUE_LIST_INSTRUCTIONS, PERSONAL_REQUIRED_INPUTS

FIXTURES = Path(__file__).parent / "fixtures"
COMPLETED = json.loads((FIXTURES / "creao_hub_run_completed.json").read_text(encoding="utf-8"))
MISSING_INPUTS = json.loads((FIXTURES / "creao_hub_run_missing_inputs.json").read_text(encoding="utf-8"))
WITH_ISSUES = json.loads((FIXTURES / "creao_hub_run_with_issue_list.json").read_text(encoding="utf-8"))
NOT_ANALYZED = json.loads((FIXTURES / "creao_agent2_run_not_analyzed.json").read_text(encoding="utf-8"))
CONTRACT_PROBE = json.loads((FIXTURES / "creao_personal_agent_contract_probe.json").read_text(encoding="utf-8"))
PERSONAL_DIAGNOSIS = json.loads((FIXTURES / "creao_personal_agent_diagnosis.json").read_text(encoding="utf-8"))
PERSONAL_INPUTS = {k: PERSONAL_DIAGNOSIS["request"]["body"]["inputs"][k] for k in PERSONAL_REQUIRED_INPUTS}
NO_SUMMARY = json.loads((FIXTURES / "creao_personal_agent_no_summary.json").read_text(encoding="utf-8"))

GOOD_INPUTS = COMPLETED["request"]["body"]["inputs"]
FAKE_KEY = "capi_fake_key_for_tests"
RUN_ID = COMPLETED["final_response"]["id"]
RUNNING = {**COMPLETED["final_response"], "status": "running", "result": None}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class FakeSession:
    """Stands in for requests.Session: returns queued responses and records calls.
    The last queued response repeats once the others are used up."""

    def __init__(self, post=(), get=()):
        self.queues = {"POST": list(post), "GET": list(get)}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        queue = self.queues[method]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    """Fake time, so timeout tests finish instantly."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_client(session, **kwargs):
    agent = AgentConfig(name="hub", app_id="app-test", api_key=FAKE_KEY, required_inputs=HUB_REQUIRED_INPUTS)
    clock = FakeClock()
    return CreaoClient(agent, session=session, sleep=clock.sleep, clock=clock, **kwargs)


def ok(payload):
    return FakeResponse(200, payload)


# --- Successful runs -------------------------------------------------------


def test_completed_run_returns_reply_and_summary():
    session = FakeSession(post=[ok(COMPLETED["start_response"])], get=[ok(RUNNING), ok(COMPLETED["final_response"])])

    result = make_client(session).run(GOOD_INPUTS)

    assert result.run_id == RUN_ID
    assert result.status == "completed"
    assert result.analysis_ran is True
    assert result.summary["issues_confirmed"] == 3
    assert result.summary["experiments_proposed"] == 3
    assert "von Neumann stability violation" in result.reply


def test_sends_the_verified_request_shape():
    session = FakeSession(post=[ok(COMPLETED["start_response"])], get=[ok(COMPLETED["final_response"])])

    make_client(session).run(GOOD_INPUTS)

    start, poll = session.calls[0], session.calls[1]
    assert start["method"] == "POST"
    assert start["url"] == "https://agent.creao.ai/api/v1/apps/app-test/runs"
    assert start["json"] == {"inputs": GOOD_INPUTS}
    assert start["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert poll["method"] == "GET"
    assert poll["url"] == f"https://agent.creao.ai/api/v1/apps/app-test/runs/{RUN_ID}"


def test_model_override_is_a_run_option_not_an_agent_input():
    session = FakeSession(post=[ok(COMPLETED["start_response"])], get=[ok(COMPLETED["final_response"])])
    make_client(session, chat_model_id="test-fast-model").run(GOOD_INPUTS)
    body = session.calls[0]["json"]
    assert body["chatModelId"] == "test-fast-model"
    assert body["inputs"] == GOOD_INPUTS
    assert "chatModelId" not in body["inputs"]


@pytest.mark.parametrize("model_id", ["", "bad model", "x" * 121, "model\nother"])
def test_invalid_model_override_is_rejected_before_a_paid_run(model_id):
    session = FakeSession()
    with pytest.raises(CreaoConfigError):
        make_client(session, chat_model_id=model_id)
    assert session.calls == []


def test_missing_inputs_reply_is_recognised():
    # CREAO marks this run "completed" even though no analysis ran.
    result = parse_run(MISSING_INPUTS["final_response"])

    assert result.status == "completed"
    assert result.analysis_ran is False
    assert result.summary == {}
    assert "project_path" in result.reply


def test_zero_counts_with_no_score_do_not_count_as_an_analysis():
    # This agent returns a summary even when nothing was analyzed.
    result = parse_run(NOT_ANALYZED["final_response"])

    assert result.summary["issues_confirmed"] == 0
    assert result.summary["reproducibility_score"] == "not_computed"
    assert result.analysis_ran is False


def test_a_zero_score_from_a_connection_test_is_not_an_analysis():
    # Numeric score of 0, but the agent echoes analysis_depth "none".
    result = parse_run(CONTRACT_PROBE["final_response"])

    assert result.summary["reproducibility_score"] == 0
    assert result.analysis_ran is False


@pytest.mark.parametrize("fixture, ran", [
    (COMPLETED, True), (WITH_ISSUES, True), (MISSING_INPUTS, False), (NOT_ANALYZED, False), (CONTRACT_PROBE, False),
    (PERSONAL_DIAGNOSIS, True), (NO_SUMMARY, True),
])
def test_every_saved_real_run_is_classified_correctly(fixture, ran):
    assert parse_run(fixture["final_response"]).analysis_ran is ran


def test_a_full_analysis_without_a_structured_summary_still_counts():
    # Run 461fed21: only {"response": ...} came back, but with the issue list we asked for.
    result = parse_run(NO_SUMMARY["final_response"])

    assert result.summary == {}
    assert len(result.issues) == 9
    assert result.analysis_ran is True


def test_issues_found_count_as_an_analysis_even_without_a_score():
    raw = {"id": "r1", "status": "completed",
           "result": {"text": json.dumps({"issues_confirmed": 1, "reproducibility_score": "n/a"})}}

    assert parse_run(raw).analysis_ran is True


def test_non_json_result_text_is_still_readable():
    raw = {"id": "r1", "status": "completed", "result": {"text": "plain words, not JSON"}}

    result = parse_run(raw)

    assert result.reply == "plain words, not JSON"
    assert result.analysis_ran is False


# --- Issue list ------------------------------------------------------------


def test_issue_list_is_extracted_from_the_reply():
    result = parse_run(WITH_ISSUES["final_response"])

    assert len(result.issues) == 6
    statuses = [i["status"] for i in result.issues]
    # The list agrees with the agent's own summary counts.
    assert statuses.count("confirmed") == result.summary["issues_confirmed"] == 2
    assert statuses.count("suspected") == result.summary["issues_suspected"] == 4
    for issue in result.issues:
        assert issue["title"] and issue["evidence"] and issue["proposed_validation"]
        assert issue["confidence"] in ("high", "medium", "low")
    assert "stability limit" in result.issues[0]["title"]


def test_issue_block_is_removed_from_the_reply_text():
    result = parse_run(WITH_ISSUES["final_response"])

    assert "```json" not in result.reply
    assert "**Verdict:**" in result.reply


def test_runs_without_a_block_have_no_issues():
    assert parse_run(COMPLETED["final_response"]).issues == []
    assert parse_run(MISSING_INPUTS["final_response"]).issues == []


def test_unknown_status_is_never_upgraded_to_confirmed():
    block = {"issues": [
        {"title": "A", "status": "likely", "confidence": "very high"},
        {"title": "B", "status": "CONFIRMED"},
        {"title": "", "status": "confirmed"},
        "not an object",
    ]}
    raw = {"id": "r1", "status": "completed", "result": {"chatText": f"Reply.\n```json\n{json.dumps(block)}\n```"}}

    issues = parse_run(raw).issues

    assert [i["title"] for i in issues] == ["A", "B"]  # untitled and non-object entries dropped
    assert [i["status"] for i in issues] == ["suspected", "suspected"]
    assert issues[0]["confidence"] is None


def test_broken_issue_block_leaves_the_reply_untouched():
    chat = 'Reply.\n```json\n{"issues": [{"title": "cut off\n```'
    raw = {"id": "r1", "status": "completed", "result": {"chatText": chat}}

    result = parse_run(raw)

    assert result.issues == []
    assert result.reply == chat


def test_hub_runs_ask_for_the_issue_list(monkeypatch):
    monkeypatch.setenv("CREAO_HUB_APP_ID", "app-test")
    monkeypatch.setenv("CREAO_HUB_KEY", FAKE_KEY)
    session = FakeSession(post=[ok(COMPLETED["start_response"])])
    client = CreaoClient(hub_agent(env_file=None), session=session)

    client.start_run(GOOD_INPUTS)

    sent = session.calls[0]["json"]["inputs"]
    assert sent["output_format"] == ISSUE_LIST_INSTRUCTIONS
    assert {k: sent[k] for k in GOOD_INPUTS} == GOOD_INPUTS


# --- Personal agents -------------------------------------------------------


@pytest.fixture
def personal_client(monkeypatch):
    monkeypatch.setenv("CREAO_AGENT_1_APP_ID", "app-personal")
    monkeypatch.setenv("CREAO_AGENT_1_KEY", FAKE_KEY)
    session = FakeSession(post=[ok(COMPLETED["start_response"])])
    return CreaoClient(personal_agent("AGENT_1", env_file=None), session=session), session


def test_personal_agent_config(personal_client):
    client, _ = personal_client

    assert client.agent.required_inputs == PERSONAL_REQUIRED_INPUTS
    assert client.agent.extra_inputs == {"output_format": ISSUE_LIST_INSTRUCTIONS}
    assert "numerical" in client.agent.input_choices["focus_areas"]


def test_real_personal_agent_diagnosis_is_parsed():
    result = parse_run(PERSONAL_DIAGNOSIS["final_response"])
    statuses = [i["status"] for i in result.issues]

    assert result.analysis_ran is True
    assert len(result.issues) == 10
    assert statuses.count("confirmed") == result.summary["issues_confirmed"] == 7
    assert statuses.count("suspected") == result.summary["issues_suspected"] == 3


def test_several_focus_areas_are_sent_as_comma_separated_text(personal_client):
    client, session = personal_client

    client.start_run(PERSONAL_INPUTS)

    sent = session.calls[0]["json"]["inputs"]
    assert sent["focus_areas"] == "numerical, reproducibility"
    assert sent["analysis_depth"] == "standard"
    assert sent["output_format"] == ISSUE_LIST_INSTRUCTIONS


@pytest.mark.parametrize("name, value, bad", [
    ("focus_areas", "numerical, vibes", ["vibes"]),
    ("analysis_depth", "extreme", ["extreme"]),
])
def test_values_outside_the_fixed_choices_are_refused_before_sending(personal_client, name, value, bad):
    client, session = personal_client

    with pytest.raises(CreaoInputError) as err:
        client.start_run({**PERSONAL_INPUTS, name: value})

    assert err.value.invalid == {name: bad}
    assert session.calls == []


def test_personal_agent_requires_analysis_depth(personal_client):
    client, session = personal_client
    inputs = {k: v for k, v in PERSONAL_INPUTS.items() if k != "analysis_depth"}

    with pytest.raises(CreaoInputError) as err:
        client.start_run(inputs)

    assert err.value.missing == ["analysis_depth"]
    assert session.calls == []


# --- Input checks (nothing sent, no credits spent) -------------------------


def test_missing_required_inputs_are_refused_before_sending():
    session = FakeSession(post=[ok(COMPLETED["start_response"])])
    inputs = {"observed_symptom": "NaN at step 262"}

    with pytest.raises(CreaoInputError) as err:
        make_client(session).start_run(inputs)

    assert err.value.missing == ["project_path", "focus_areas"]
    assert session.calls == []


def test_blank_input_counts_as_missing():
    session = FakeSession(post=[ok(COMPLETED["start_response"])])

    with pytest.raises(CreaoInputError) as err:
        make_client(session).start_run({**GOOD_INPUTS, "focus_areas": "   "})

    assert err.value.missing == ["focus_areas"]
    assert session.calls == []


# --- Runs that don't succeed -----------------------------------------------


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_or_cancelled_run_raises(status):
    finished = {**COMPLETED["final_response"], "status": status, "errorCode": "AGENT_ERROR", "result": None}
    session = FakeSession(post=[ok(COMPLETED["start_response"])], get=[ok(finished)])

    with pytest.raises(CreaoRunFailed) as err:
        make_client(session).run(GOOD_INPUTS)

    assert err.value.run_id == RUN_ID
    assert err.value.status == status
    assert err.value.error_code == "AGENT_ERROR"


def test_timeout_raises_with_run_id():
    session = FakeSession(post=[ok(COMPLETED["start_response"])], get=[ok(RUNNING)])

    with pytest.raises(CreaoTimeout) as err:
        make_client(session, poll_interval=5, wait_timeout=60).run(GOOD_INPUTS)

    assert err.value.run_id == RUN_ID
    assert RUN_ID in str(err.value)


def test_rate_limit_while_polling_keeps_waiting():
    session = FakeSession(
        post=[ok(COMPLETED["start_response"])],
        get=[FakeResponse(429, {"error": "RATE_LIMITED"}), ok(COMPLETED["final_response"])],
    )

    result = make_client(session).run(GOOD_INPUTS)

    assert result.status == "completed"


def test_start_is_never_retried():
    # Each start costs credits, so a failed start must not be repeated.
    session = FakeSession(post=[FakeResponse(500, None, text="server error")])

    with pytest.raises(CreaoRequestError) as err:
        make_client(session).start_run(GOOD_INPUTS)

    assert err.value.status_code == 500
    assert len(session.calls) == 1


def test_bad_key_gives_a_clear_error():
    session = FakeSession(post=[FakeResponse(401, {"error": "AUTHENTICATION_REQUIRED"})])

    with pytest.raises(CreaoRequestError) as err:
        make_client(session).start_run(GOOD_INPUTS)

    assert err.value.status_code == 401
    assert "CREAO_HUB_KEY" in str(err.value)


# --- The key never leaks ---------------------------------------------------


def test_key_is_scrubbed_from_network_errors():
    session = FakeSession(post=[requests.ConnectionError(f"connection refused, header was Bearer {FAKE_KEY}")])

    with pytest.raises(CreaoRequestError) as err:
        make_client(session).start_run(GOOD_INPUTS)

    assert FAKE_KEY not in str(err.value)
    assert "[redacted]" in str(err.value)


def test_key_is_scrubbed_from_http_error_bodies():
    session = FakeSession(post=[FakeResponse(500, None, text=f"bad request with {FAKE_KEY}")])

    with pytest.raises(CreaoRequestError) as err:
        make_client(session).start_run(GOOD_INPUTS)

    assert FAKE_KEY not in str(err.value)


def test_key_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    session = FakeSession(post=[ok(COMPLETED["start_response"])], get=[ok(RUNNING), ok(COMPLETED["final_response"])])

    make_client(session).run(GOOD_INPUTS)

    assert caplog.text  # the adapter does log run progress
    assert FAKE_KEY not in caplog.text


def test_key_is_hidden_from_repr():
    agent = AgentConfig(name="hub", app_id="app-test", api_key=FAKE_KEY)

    assert FAKE_KEY not in repr(agent)


# --- Configuration ---------------------------------------------------------


def test_config_reads_hub_variables(monkeypatch):
    monkeypatch.setenv("CREAO_HUB_APP_ID", "app-from-env")
    monkeypatch.setenv("CREAO_HUB_KEY", FAKE_KEY)
    monkeypatch.delenv("CREAO_BASE_URL", raising=False)

    agent = hub_agent(env_file=None)

    assert agent.app_id == "app-from-env"
    assert agent.api_key == FAKE_KEY
    assert agent.required_inputs == HUB_REQUIRED_INPUTS
    assert agent.base_url == "https://agent.creao.ai/api/v1"
    assert agent.extra_inputs == {"output_format": ISSUE_LIST_INSTRUCTIONS}


def test_config_names_missing_variables(monkeypatch):
    monkeypatch.delenv("CREAO_HUB_APP_ID", raising=False)
    monkeypatch.delenv("CREAO_HUB_KEY", raising=False)

    with pytest.raises(CreaoConfigError) as err:
        hub_agent(env_file=None)

    assert "CREAO_HUB_APP_ID" in str(err.value)
    assert "CREAO_HUB_KEY" in str(err.value)
