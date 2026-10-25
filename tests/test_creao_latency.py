"""Clock-controlled latency accounting and concurrent, ordered research lookups."""
import json
import threading

import pytest

from backend.creao.client import AgentConfig, CreaoClient, CreaoRequestError, CreaoTimeout
from backend.orchestration.chat import _answer
from backend.orchestration.toolkit import Toolkit


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


FINISHED = {"id": "run-1", "status": "completed", "createdAt": "2026-10-25T00:00:00Z",
            "startedAt": "2026-10-25T00:00:00.100Z", "completedAt": "2026-10-25T00:00:01.100Z",
            "result": {"text": "dt = 0.02"}}


def client_with(clock, get_responses):
    class Session:
        calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            clock.now += 0.2 if method == "POST" else 0.1
            return Response({"id": "run-1"}) if method == "POST" else get_responses.pop(0)
    session = Session()
    client = CreaoClient(AgentConfig("hub", "app-1", "capi_FAKE_NEVER_LOG"), session=session,
                         clock=clock, sleep=clock.sleep)
    return client, session


def test_latency_distinguishes_http_sleep_and_vendor_execution_without_credentials():
    clock = Clock()
    client, session = client_with(clock, [Response({"id": "run-1", "status": "running"}), Response(FINISHED)])
    result = client.run({"question": "time step"})
    timing = result.timings
    assert timing["submit_ms"] == 200
    assert timing["status_http_ms"] == 200
    assert timing["poll_sleep_ms"] == 1000
    assert timing["total_ms"] == 1400
    assert timing["status_requests"] == 2
    assert timing["vendor_queue_ms"] == 100
    assert timing["vendor_execution_ms"] == 1000
    assert timing["observation_overhead_ms"] == 300
    assert timing["poll_wait_overlaps_execution"] is True
    assert "capi_" not in json.dumps(timing) and "app-1" not in json.dumps(timing)
    assert session.calls[0][1].endswith("/apps/app-1/runs")
    assert session.calls[0][2]["json"] == {"inputs": {"question": "time step"}}


def test_missing_or_invalid_vendor_times_are_explicitly_unknown():
    client, _ = client_with(Clock(), [Response({**FINISHED, "createdAt": None, "completedAt": "invalid"})])
    timing = client.run({}).timings
    assert timing["vendor_queue_ms"] is None
    assert timing["vendor_execution_ms"] is None
    assert timing["observation_overhead_ms"] is None


def test_new_run_and_resume_do_not_reuse_previous_latency():
    clock = Clock()
    client, _ = client_with(clock, [Response(FINISHED), Response(FINISHED), Response({**FINISHED, "id": "resumed"})])
    assert client.run({}).timings["status_requests"] == 1
    assert client.run({}).timings["status_requests"] == 1
    resumed = client.wait_for_result("resumed")
    assert resumed.timings["submit_ms"] == 0
    assert resumed.timings["total_ms"] == 100


def test_rate_limits_back_off_without_creating_additional_paid_runs():
    client, session = client_with(Clock(), [Response({}, 429), Response({}, 429), Response(FINISHED)])
    result = client.run({})
    assert result.timings["rate_limited_polls"] == 2
    assert result.timings["poll_sleep_ms"] == 4000
    assert [method for method, _, _ in session.calls].count("POST") == 1


def test_server_retry_after_is_honored_and_deadline_prevents_an_extra_poll():
    limited = Response({}, 429)
    limited.headers = {"Retry-After": "8"}
    clock = Clock()
    client, session = client_with(clock, [limited])
    client.wait_timeout = 2.5
    with pytest.raises(CreaoTimeout):
        client.run({})
    assert [method for method, _, _ in session.calls] == ["POST", "GET"]
    assert clock.now == pytest.approx(2.7)


@pytest.mark.parametrize("model", ["test-fast-model", None])
def test_personal_chat_uses_existing_chat_model_preference_without_changing_the_agent_or_diagnoses(tmp_path, monkeypatch, model):
    import importlib
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from backend.creao.client import ChatSettings

    app_module = importlib.import_module("backend.api.app")
    chosen = []
    monkeypatch.setattr(app_module, "personal_agent", lambda prefix: prefix)
    def build(config, **kwargs):
        chosen.append((config, kwargs))
        return SimpleNamespace(chat_model_id=kwargs.get("chat_model_id"), run=lambda inputs: SimpleNamespace(
            run_id="test-run", reply="The time step is recorded as 0.02.", summary={}, issues=[], analysis_ran=False))
    monkeypatch.setattr(app_module, "CreaoClient", build)
    api = TestClient(app_module.create_app(data_dir=tmp_path, chat_setup=lambda: ChatSettings("CHAT", model)))
    token = api.post("/sign-in", json={"member_id": "researcher_a"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    response = api.post("/personal-agent/chat", headers=headers, json={"project_id": "heat-diffusion", "message": "What time step?"})
    assert response.status_code == 202
    assert chosen == [("AGENT_1", {"poll_interval": 1.0, "chat_model_id": model})]
    job = api.get("/jobs/" + response.json()["job_id"], headers=headers).json()
    assert job["diagnosis"]["model"] == (model or "saved")
    assert job["diagnosis"]["agent_slot"] == "AGENT_1"
    assert api.post("/diagnose", headers=headers, json={"project_id": "heat-diffusion", "symptom": "Residual spike"}).status_code == 202
    assert chosen[-1] == ("AGENT_1", {})


def test_the_shared_pace_stays_under_creaos_limit_and_honours_a_hold():
    from backend.creao.client import Pace
    clock = Clock()
    pace = Pace(per_minute=3)
    assert [pace.wait(clock, clock.sleep) for _ in range(3)] == [0.0, 0.0, 0.0]  # within the limit: no waiting
    assert pace.wait(clock, clock.sleep) == 60  # the fourth waits until the first is a minute old
    pace.hold(33, clock)  # a 429 said "Retry-After: 33"
    started = clock.now
    pace.wait(clock, clock.sleep)
    assert clock.now - started == pytest.approx(33)


def test_a_new_run_is_first_checked_when_it_could_be_done_and_a_resumed_one_at_once():
    from backend.creao.client import Pace
    clock = Clock()
    client, session = client_with(clock, [Response(FINISHED), Response({**FINISHED, "id": "resumed"})])
    client._pace, client._first_check = Pace(), 6.0
    timing = client.run({}).timings
    assert timing["status_requests"] == 1 and timing["poll_sleep_ms"] == 5800  # 6 s after the start, less the 0.2 s POST
    resumed = client.wait_for_result("resumed")  # e.g. after a restart: it may already be done
    assert resumed.timings["poll_sleep_ms"] == 0 and resumed.timings["status_requests"] == 1


def test_the_first_check_learns_each_agents_usual_time(monkeypatch):
    from backend.creao import client as creao
    monkeypatch.setattr(creao, "_usual_seconds", {})
    clock = Clock()
    client, _ = client_with(clock, [Response({**FINISHED, "completedAt": "2026-10-25T00:00:20.100Z"})])
    client._first_check = None
    assert client._first_wait() == pytest.approx(creao.FIRST_CHECK_SHARE * creao.USUAL_RUN_SECONDS)  # nothing known yet
    client.run({})  # CREAO took 20 s
    assert creao._usual_seconds["app-1"] == pytest.approx(20)
    assert client._first_wait() == creao.FIRST_CHECK_RANGE[1]  # 75% of 20 s, capped at 12 s
    creao._usual_seconds["app-1"] = 10
    assert client._first_wait() == pytest.approx(7.5)
    creao._usual_seconds["app-1"] = 1
    assert client._first_wait() == creao.FIRST_CHECK_RANGE[0]  # never sooner than 2 s


def test_each_agent_has_its_own_pace_shared_by_its_clients():
    from backend.creao.client import AgentConfig, CreaoClient
    hub_one, hub_two = (CreaoClient(AgentConfig("hub", "app-hub", "capi_FAKE")) for _ in range(2))
    agent = CreaoClient(AgentConfig("agent", "app-agent", "capi_FAKE"))
    assert hub_one._pace is hub_two._pace and hub_one._pace is not agent._pace
    assert hub_one._first_check is None  # the real clock: the first check is timed to the agent


def test_a_rate_limit_holds_every_client_that_shares_the_pace():
    from backend.creao.client import Pace
    clock, pace = Clock(), Pace()
    limited = Response({}, 429)
    limited.headers = {"Retry-After": "30"}
    client, _ = client_with(clock, [limited, Response(FINISHED)])
    client._pace = pace
    client.run({})
    other, _ = client_with(clock, [Response(FINISHED)])
    other._pace = pace
    before = clock.now
    other.run({})
    assert clock.now - before < 1  # the hold has passed: the other chat goes straight through
    pace.hold(30, clock)
    third, _ = client_with(clock, [Response(FINISHED)])
    third._pace = pace
    assert third.run({}).timings["paced_ms"] == pytest.approx(30_000)  # ...and waits out a new one


def test_timing_metadata_reaches_the_chat_result():
    client, _ = client_with(Clock(), [Response(FINISHED)])
    result = _answer(client, {}, 0)
    assert result["reply"] == "dt = 0.02"
    assert result["latency"]["vendor_execution_ms"] == 1000


def test_independent_reads_overlap_and_keep_requested_result_order():
    tools = Toolkit(None, {}, "p", None, None)
    entered = threading.Barrier(3, timeout=3)
    def read(name, args):
        entered.wait()
        return {"query": args["query"]}, "read"
    tools._one = read
    calls = tools.run([{"tool": "search_super_brain", "query": value} for value in ("first", "second", "third")])
    assert [call["result"]["query"] for call in calls] == ["first", "second", "third"]
    assert all(call["ok"] for call in calls)


@pytest.mark.parametrize("controlled", ["propose_research_relationship", "get_research_context", "find_related_experiments"])
def test_batches_with_writes_or_context_reconciliation_keep_original_execution_order(controlled):
    tools = Toolkit(None, {}, "p", None, None)
    seen = []
    def operation(name, args):
        seen.append(name)
        return {}, "done"
    tools._one = operation
    requests = [{"tool": name} for name in ("get_simulation_runs", controlled, "get_simulation_runs")]
    assert all(call["ok"] for call in tools.run(requests))
    assert seen == [request["tool"] for request in requests]


def test_parallel_reads_still_enforce_request_and_result_size_limits(monkeypatch):
    tools = Toolkit(None, {}, "p", None, None)
    tools._one = lambda name, args: ({"value": "a" * 70}, "read")
    monkeypatch.setattr("backend.orchestration.toolkit.MAX_ROUND_CHARS", 150)
    calls = tools.run([{"tool": "get_simulation_runs"}] * 7)
    assert [call["ok"] for call in calls] == [True, False, False, False, False, False, False]
    assert "at most" in calls[-1]["error"]
