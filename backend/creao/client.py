"""CREAO API adapter: the only module in ExpandRG that talks to CREAO.

Uses the API Trigger contract verified in backend/README.md:
    POST {base}/runs        {"appId": ..., "inputs": {...}}  -> {"id": ...}
    GET  {base}/runs/{id}   poll until status is completed, failed or cancelled

The API key is never logged, never included in error messages and never shown
in repr() output.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://agent.creao.ai/api/v1"
# backend/.env, or a research group's own keys (run_demo.py --group sets EXPANDRG_ENV_FILE)
DEFAULT_ENV_FILE = Path(os.environ.get("EXPANDRG_ENV_FILE") or Path(__file__).resolve().parents[1] / ".env")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

# Required inputs of the configured hub agent. A run without them still
# "completes" (and costs credits) but only reports what is missing.
HUB_REQUIRED_INPUTS = ("project_path", "observed_symptom", "focus_areas")
# The dedicated Super Brain chat agent (made in agent.creao.ai) takes just these two.
CHAT_REQUIRED_INPUTS = ("question", "context")
# Measured at about a third of the hub's saved model's time (Documents/benchmarks/).
FAST_CHAT_MODEL_ID = "gemini-3-flash"

# Summary counts the hub agent returns in result.text when an analysis ran.
SUMMARY_FIELDS = (
    "issues_confirmed",
    "issues_suspected",
    "blocking_count",
    "reproducibility_score",
    "experiments_proposed",
)

# Extra input sent with every hub agent run. The agent reads inputs it doesn't
# declare, and with this one it ends its reply with the issues as a JSON list
# (verified 2026-10-23, run 790fe5fd).
ISSUE_LIST_INSTRUCTIONS = (
    "After your normal reply, end with one fenced ```json code block and nothing after it. "
    'The block must be a JSON object of the form {"issues": [...]} with one entry per issue '
    "you found, confirmed and suspected. Each entry: "
    '{"title": short name, "status": "confirmed" or "suspected", '
    '"confidence": "high", "medium" or "low", '
    '"evidence": what in the provided data supports it, '
    '"proposed_validation": how the researcher can check it}. '
    "Put no other text inside the block."
)
ISSUE_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
ISSUE_TEXT_FIELDS = ("title", "evidence", "proposed_validation")
CONFIDENCE_LEVELS = ("high", "medium", "low")
ANALYSIS_DEPTHS = ("triage", "standard", "deep")

# Inputs of the personal agents (AGENT_1, AGENT_2), verified 2026-10-23 on runs
# 8eb4dc7d and 7bcbb28f. Several focus areas are sent as comma-separated text
# (verified on run aaa9f564).
PERSONAL_REQUIRED_INPUTS = ("project_path", "observed_symptom", "focus_areas", "analysis_depth")
FOCUS_AREAS = ("numerical", "reproducibility", "data_integrity", "performance", "visualization", "all")
PERSONAL_INPUT_CHOICES = {"focus_areas": FOCUS_AREAS, "analysis_depth": ANALYSIS_DEPTHS}


class CreaoError(Exception):
    """Base class for every error raised by this adapter."""


class CreaoConfigError(CreaoError):
    """A required environment variable is missing."""


class CreaoInputError(CreaoError):
    """Required agent inputs are missing or not an allowed choice; nothing was sent to CREAO."""

    def __init__(self, missing: list[str], invalid: dict[str, list[str]] | None = None):
        self.missing = missing
        self.invalid = invalid or {}
        parts = [f"Missing required CREAO inputs: {', '.join(missing)}."] if missing else []
        parts += [f"Not an allowed {name}: {', '.join(values)}." for name, values in self.invalid.items()]
        super().__init__(" ".join(parts) + " Nothing was sent.")


class CreaoRequestError(CreaoError):
    """CREAO could not be reached or answered with an HTTP error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CreaoRunFailed(CreaoError):
    """The run finished with status failed or cancelled."""

    def __init__(self, run_id: str, status: str, error_code: str | None):
        super().__init__(f"CREAO run {run_id} ended with status '{status}' (error code: {error_code}).")
        self.run_id = run_id
        self.status = status
        self.error_code = error_code


class CreaoTimeout(CreaoError):
    """The run did not finish in time. It may still finish on CREAO's side."""

    def __init__(self, run_id: str, waited_seconds: float):
        super().__init__(
            f"CREAO run {run_id} did not finish within {waited_seconds:.0f}s. "
            "It may still complete; check it later with this run ID."
        )
        self.run_id = run_id


@dataclass(frozen=True)
class AgentConfig:
    """One CREAO agent: its app ID and API Trigger key (capi_)."""

    name: str
    app_id: str
    api_key: str = field(repr=False)
    required_inputs: tuple[str, ...] = ()
    base_url: str = DEFAULT_BASE_URL
    extra_inputs: dict[str, str] = field(default_factory=dict, compare=False)  # sent with every run
    # Fixed-choice inputs; a value may list several choices separated by commas.
    input_choices: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False)

    @classmethod
    def from_env(
        cls,
        prefix: str,
        required_inputs: tuple[str, ...] = (),
        env_file: Path | None = DEFAULT_ENV_FILE,
        extra_inputs: dict[str, str] | None = None,
        input_choices: dict[str, tuple[str, ...]] | None = None,
    ) -> AgentConfig:
        """Read CREAO_<prefix>_APP_ID and CREAO_<prefix>_KEY, e.g. prefix "HUB" or "AGENT_1"."""
        if env_file is not None:
            load_dotenv(env_file)  # does not override variables that are already set
        app_var, key_var = f"CREAO_{prefix}_APP_ID", f"CREAO_{prefix}_KEY"
        missing = [v for v in (app_var, key_var) if not os.environ.get(v, "").strip()]
        if missing:
            raise CreaoConfigError(f"Set {', '.join(missing)} in backend/.env (see backend/.env.example).")
        return cls(
            name=prefix.lower(),
            app_id=os.environ[app_var].strip(),
            api_key=os.environ[key_var].strip(),
            required_inputs=required_inputs,
            base_url=os.environ.get("CREAO_BASE_URL", "").strip() or DEFAULT_BASE_URL,
            extra_inputs=dict(extra_inputs or {}),
            input_choices=dict(input_choices or {}),
        )


def hub_agent(env_file: Path | None = DEFAULT_ENV_FILE) -> AgentConfig:
    """Config for the configured hub agent: its required inputs, plus the
    request to end each reply with a JSON issue list."""
    return AgentConfig.from_env(
        "HUB", HUB_REQUIRED_INPUTS, env_file, extra_inputs={"output_format": ISSUE_LIST_INSTRUCTIONS}
    )


@dataclass(frozen=True)
class ChatSettings:
    """Which agent answers Super Brain chat, and with which CREAO model."""

    slot: str  # "CHAT" (the dedicated chat agent) or "HUB"
    model_id: str | None  # sent as chatModelId; None keeps the agent's saved model


def chat_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> ChatSettings:
    """Super Brain chat runs on the dedicated chat agent once CREAO_CHAT_APP_ID and
    CREAO_CHAT_KEY are set (an ordinary capi_ app key), otherwise on the hub.
    CREAO_CHAT_MODEL_ID picks the model, a fast one by default; "saved" keeps the
    agent's own model."""
    if env_file is not None:
        load_dotenv(env_file)
    dedicated = all(os.environ.get(v, "").strip() for v in ("CREAO_CHAT_APP_ID", "CREAO_CHAT_KEY"))
    model = os.environ.get("CREAO_CHAT_MODEL_ID", "").strip() or FAST_CHAT_MODEL_ID
    return ChatSettings("CHAT" if dedicated else "HUB", None if model.lower() == "saved" else model)


def chat_agent(env_file: Path | None = DEFAULT_ENV_FILE) -> AgentConfig:
    """Config for the dedicated Super Brain chat agent: a question and its context, nothing else."""
    return AgentConfig.from_env("CHAT", CHAT_REQUIRED_INPUTS, env_file)


def personal_agent(prefix: str, env_file: Path | None = DEFAULT_ENV_FILE) -> AgentConfig:
    """Config for a researcher's personal agent (e.g. prefix "AGENT_1"): its required
    inputs, its fixed choices, and the request to end each reply with a JSON issue list."""
    return AgentConfig.from_env(
        prefix,
        PERSONAL_REQUIRED_INPUTS,
        env_file,
        extra_inputs={"output_format": ISSUE_LIST_INSTRUCTIONS},
        input_choices=PERSONAL_INPUT_CHOICES,
    )


@dataclass
class RunResult:
    """A finished CREAO run, unpacked."""

    run_id: str
    status: str
    reply: str  # prose reply (result.chatText)
    summary: dict[str, Any]  # summary counts; empty when no analysis ran
    analysis_ran: bool  # False when the agent only answered (e.g. reported missing inputs)
    # Issues from the reply's JSON block. "confirmed" is the agent's own label,
    # not researcher verification. Empty if the reply had no valid block.
    issues: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False)


def parse_run(raw: dict[str, Any]) -> RunResult:
    """Unpack a GET /runs/{id} response. result.text is a JSON string whose shape
    depends on the outcome: {"response": ...} or the inputs plus summary counts.
    The issue list, when present, is a JSON block at the end of result.chatText."""
    result = raw.get("result") or {}
    text = result.get("text") or ""
    try:
        structured = json.loads(text) if text else {}
    except ValueError:
        structured = {}
    if not isinstance(structured, dict):
        structured = {}

    summary = {k: structured[k] for k in SUMMARY_FIELDS if k in structured}
    reply = result.get("chatText") or structured.get("response") or text
    reply, issues = _extract_issues(reply)
    return RunResult(
        run_id=raw.get("id", ""),
        status=raw.get("status", ""),
        reply=reply,
        summary=summary,
        analysis_ran=_analysis_ran(structured, summary, issues),
        issues=issues,
        raw=raw,
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _analysis_ran(structured: dict[str, Any], summary: dict[str, Any], issues: list[dict[str, Any]]) -> bool:
    """Whether the agent actually analyzed anything. Agents usually echo the
    analysis_depth they used: a real depth on real analyses, "none" or "not_run" on
    runs that only reported their inputs, which can still carry summary counts of
    zero and a reproducibility_score of 0 (runs 829188d5, 8eb4dc7d). Sometimes a full
    analysis comes back with no structured summary at all, only prose and the issue
    list we asked for (run 461fed21), so a returned issue list also counts."""
    depth = structured.get("analysis_depth")
    if depth is not None:
        return depth in ANALYSIS_DEPTHS
    if issues:
        return True
    return any(_is_number(v) and v > 0 for k, v in summary.items() if k != "reproducibility_score")


def extract_json_block(reply: str, key: str) -> tuple[str, Any]:
    """Split the last ```json {"<key>": ...} block off a reply. Returns the reply
    without it and the value under key, or the reply unchanged and None."""
    for block in reversed(list(ISSUE_BLOCK.finditer(reply))):
        try:
            data = json.loads(block.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and key in data:
            return (reply[: block.start()] + reply[block.end() :]).strip(), data[key]
    return reply, None


def _extract_issues(reply: str) -> tuple[str, list[dict[str, Any]]]:
    """Split the ```json {"issues": [...]} block off the reply. Agent output is
    untrusted: entries without a title are dropped, and anything not labelled
    exactly "confirmed" is treated as suspected."""
    cleaned, entries = extract_json_block(reply, "issues")
    if not isinstance(entries, list):
        return reply, []

    issues = []
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("title", "")).strip():
            continue
        issue = {k: str(entry.get(k, "")).strip() for k in ISSUE_TEXT_FIELDS}
        issue["status"] = "confirmed" if entry.get("status") == "confirmed" else "suspected"
        confidence = entry.get("confidence")
        issue["confidence"] = confidence if confidence in CONFIDENCE_LEVELS else None
        issues.append(issue)
    return cleaned, issues


class CreaoClient:
    """Starts runs for one agent and waits for their results."""

    def __init__(
        self,
        agent: AgentConfig,
        session: requests.Session | None = None,
        poll_interval: float = 5.0,
        wait_timeout: float = 300.0,
        request_timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        chat_model_id: str | None = None,
    ):
        if chat_model_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", chat_model_id):
            raise CreaoConfigError("CREAO_HUB_CHAT_MODEL_ID must be a valid model identifier.")
        self.agent = agent
        self.session = session or requests.Session()
        self.poll_interval = poll_interval
        self.wait_timeout = wait_timeout
        self.request_timeout = request_timeout
        self._sleep = sleep
        self._clock = clock
        self.chat_model_id = chat_model_id

    def run(self, inputs: dict[str, Any]) -> RunResult:
        """Start a run and wait for it to finish."""
        return self.wait_for_result(self.start_run(inputs))

    def start_run(self, inputs: dict[str, Any]) -> str:
        """Start a run and return its ID. Never retried automatically: every start costs credits."""
        missing = [k for k in self.agent.required_inputs if not str(inputs.get(k, "")).strip()]
        invalid = {}
        for name, choices in self.agent.input_choices.items():
            if str(inputs.get(name, "")).strip():
                bad = [v.strip() for v in str(inputs[name]).split(",") if v.strip() not in choices]
                if bad:
                    invalid[name] = bad
        if missing or invalid:
            raise CreaoInputError(missing, invalid)

        payload = {**self.agent.extra_inputs, **inputs}  # the caller's inputs win
        body = {"appId": self.agent.app_id, "inputs": payload}
        if self.chat_model_id:
            body["chatModelId"] = self.chat_model_id
        data = self._request("POST", "/runs", json=body)
        run_id = data.get("id")
        if not run_id:
            raise CreaoRequestError("CREAO accepted the run but returned no run ID.")
        log.info("CREAO run %s started for agent '%s'", run_id, self.agent.name)
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Fetch a run's current state. Free: checking status uses no credits."""
        return self._request("GET", f"/runs/{run_id}")

    def wait_for_result(self, run_id: str) -> RunResult:
        """Poll until the run finishes. Raises CreaoRunFailed or CreaoTimeout."""
        started = self._clock()
        while True:
            try:
                raw = self.get_run(run_id)
            except CreaoRequestError as exc:
                if exc.status_code != 429:  # rate limited: keep waiting, anything else is fatal
                    raise
                raw = {}
            status = raw.get("status")
            if status in TERMINAL_STATUSES:
                break
            waited = self._clock() - started
            if waited >= self.wait_timeout:
                raise CreaoTimeout(run_id, waited)
            self._sleep(self.poll_interval)

        result = parse_run(raw)
        log.info("CREAO run %s finished with status '%s'", run_id, result.status)
        if result.status != "completed":
            raise CreaoRunFailed(run_id, result.status, raw.get("errorCode"))
        return result

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.agent.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.agent.api_key}", "Content-Type": "application/json"}
        try:
            response = self.session.request(method, url, headers=headers, timeout=self.request_timeout, **kwargs)
        except requests.RequestException as exc:
            raise CreaoRequestError(self._scrub(f"Could not reach CREAO: {exc}")) from None

        if response.status_code in (401, 403):
            raise CreaoRequestError(
                f"CREAO rejected the API key for agent '{self.agent.name}' (HTTP {response.status_code}). "
                f"Check CREAO_{self.agent.name.upper()}_KEY in backend/.env.",
                response.status_code,
            )
        if response.status_code == 429:
            raise CreaoRequestError("CREAO rate limit reached (HTTP 429).", 429)
        if not 200 <= response.status_code < 300:
            raise CreaoRequestError(
                self._scrub(f"CREAO returned HTTP {response.status_code}: {response.text[:200]}"),
                response.status_code,
            )
        try:
            return response.json()
        except ValueError:
            raise CreaoRequestError(f"CREAO returned a non-JSON response (HTTP {response.status_code}).") from None

    def _scrub(self, message: str) -> str:
        if not self.agent.api_key:
            return message
        return message.replace(self.agent.api_key, "[redacted]")
