"""ExpandRG backend API.

Run locally (binds to this computer only):
    python -m uvicorn backend.api.app:app --port 8000

Sign-in is a prototype: pick a demo member (POST /sign-in) and send the
token as "Authorization: Bearer <token>".
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from threading import Lock
import time
from typing import Any, Callable, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.auth.permissions import PermissionDenied, context_project_ids, require_project_member
from backend.auth.sessions import Sessions
from backend.creao import ChatSettings, CreaoClient, chat_agent, chat_settings, hub_agent, personal_agent
from backend.db.database import Database
from backend.knowledge_sharing.offboarding import (
    OFFBOARDING_POLICY,
    OffboardingError,
    handoff_history,
    offboard_member,
)
from backend.knowledge_sharing.publishing import (
    DraftAlreadyDecided,
    DraftNotFound,
    InvalidDraft,
    approve_draft,
    reject_draft,
)
from backend.memory.private_memory import PrivateMemory
from backend.monitoring.alerts import AlertStore, InvalidAlert
from backend.monitoring.runs import (
    MAX_METRICS_PER_RUN,
    RunNotFound,
    add_report,
    create_run,
    get_visible_run,
    recent_alerts,
    run_detail,
    visible_runs,
)
from backend.memory.shared_findings import (
    get_readable_finding,
    list_project_findings,
    list_readable_findings,
    related_findings,
    search_findings,
    select_chat_findings,
)
from backend.orchestration.diagnosis import (
    MAX_CONTEXT_FINDINGS,
    EvidenceRejected,
    known_context_from,
    prepare_diagnosis,
    record_diagnosis,
    run_diagnosis,
)
from backend.orchestration.evidence_policy import MAX_ITEMS, redact_secrets
from backend.orchestration.jobs import JobStore, run_job
from backend.orchestration.chat import (
    DEFAULT_AGENT_RULES, MAX_RULE_CHARS, knowledge_context, rule_command, run_chat,
)
from backend.orchestration.overview import context_text, prepare_overview, run_overview
from backend.workspaces.demo import seed_demo
from backend.workspaces.store import InvalidProject, create_project, get_member, list_members, projects_for_member


class EvidenceIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="code", max_length=20)
    excerpt: str
    truncated: bool = False


class DiagnoseIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    symptom: str = Field(min_length=1, max_length=4000)
    focus_areas: list[str] = Field(default_factory=lambda: ["numerical"], max_length=6)
    analysis_depth: str = "standard"
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=MAX_ITEMS)
    use_super_brain: bool = True  # look up earlier approved findings and give them to the agent


class SignInIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=64)


class ChatIn(BaseModel):
    model_config = {"extra": "forbid"}
    project_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    scope: Literal["project", "all"] = "project"


class AgentRulesIn(BaseModel):
    instructions: str = Field(max_length=MAX_RULE_CHARS)


class OverviewExcerptIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="code outline", max_length=30)
    excerpt: str
    truncated: bool = False


class OverviewIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    tree: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    excerpts: list[OverviewExcerptIn] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class ProjectIn(BaseModel):
    id: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    member_ids: list[str] = Field(default_factory=list, max_length=50)


class ApproveIn(BaseModel):
    """Optional edits made before approving; anything left out keeps the draft's value."""
    title: str | None = None
    summary: str | None = None
    category: str | None = None
    verification_status: str | None = None


class AlertIn(BaseModel):
    severity: str
    check: str
    message: str = Field(max_length=2000)
    metric: str | None = Field(default=None, max_length=100)
    line_number: int | None = None
    line: str | None = Field(default=None, max_length=2000)
    step: int | None = None


class AlertsIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    run_label: str = Field(min_length=1, max_length=100)
    alerts: list[AlertIn] = Field(max_length=50)


class RunIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    run_label: str = Field(min_length=1, max_length=200)


class RunPointIn(BaseModel):
    step: int = Field(ge=0)
    values: dict[str, float | None] = Field(max_length=MAX_METRICS_PER_RUN)  # None: NaN or Inf in the log


class RunReportIn(BaseModel):
    points: list[RunPointIn] = Field(default_factory=list, max_length=2000)
    alerts: list[AlertIn] = Field(default_factory=list, max_length=50)
    stopped: bool = False  # the researcher stopped watching


class SettingsIn(BaseModel):
    save_alert_history: bool


class OffboardIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=64)


def create_app(
    client_factory: Callable[[str], Any] | None = None,
    jobs: JobStore | None = None,
    data_dir: str | Path | None = None,
    alerts: AlertStore | None = None,
    chat_setup: Callable[[], ChatSettings] | None = None,
) -> FastAPI:
    """client_factory(agent_env_prefix) builds the CREAO client for a member's
    personal agent; tests pass a fake one. The databases are created (with the
    demo workspace) on first use. Alerts are kept in memory only.
    chat_setup() says which agent and model answer Super Brain chat: from
    backend/.env with real CREAO, the hub with its saved model with a fake one."""
    app = FastAPI(title="ExpandRG backend", version="0.1.0")
    factory = client_factory or (lambda prefix: CreaoClient(hub_agent() if prefix == "HUB" else personal_agent(prefix)))
    chat_choice = chat_setup or (chat_settings if client_factory is None else (lambda: ChatSettings("HUB", None)))
    chat_lock = Lock()
    store = jobs or JobStore()
    alert_store = alerts or AlertStore()
    db = Database(data_dir, seed=seed_demo)
    sessions = Sessions()

    def current_member(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """The signed-in member, re-read on every request so offboarding takes effect at once."""
        token = (authorization or "").removeprefix("Bearer ").strip()
        member_id = sessions.member_for(token) if token else None
        if member_id is None:
            raise HTTPException(status_code=401, detail="Sign in first: POST /sign-in")
        with closing(db.shared()) as conn:
            member = get_member(conn, member_id)
        if member is None or member["status"] != "active":
            raise HTTPException(status_code=403, detail="This member no longer has access.")
        return member

    def require_member_of(conn: Any, member_id: str, project_id: str) -> None:
        try:
            require_project_member(conn, member_id, project_id)
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are not a member of that project.") from None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/demo/members")
    def demo_members() -> list[dict[str, Any]]:
        """Who can sign in (prototype only)."""
        with closing(db.shared()) as conn:
            return [{k: m[k] for k in ("id", "display_name", "role")} for m in list_members(conn)]

    @app.post("/sign-in")
    def sign_in(request: SignInIn) -> dict[str, Any]:
        with closing(db.shared()) as conn:
            member = get_member(conn, request.member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="No member with that ID.")
        if member["status"] != "active":
            raise HTTPException(status_code=403, detail="This member no longer has access.")
        return {"token": sessions.sign_in(member["id"]), "member": member}

    @app.get("/me")
    def me(member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        with closing(db.shared()) as conn:
            projects = projects_for_member(conn, member["id"])
        return {"member": member, "projects": projects}

    def project_and_agent(member: dict[str, Any], project_id: str) -> tuple[str, str]:
        """The project's name and the member's personal agent slot, after checking access."""
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
            project_name = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()[0]
            agent = conn.execute(
                "SELECT creao_env_prefix FROM agents WHERE member_id = ? AND status = 'active'", (member["id"],)
            ).fetchone()
        if agent is None:
            raise HTTPException(status_code=403, detail="You have no active personal agent.")
        return project_name, agent["creao_env_prefix"]

    @app.post("/diagnose", status_code=202)
    def start_diagnosis(
        request: DiagnoseIn, background: BackgroundTasks, member: dict[str, Any] = Depends(current_member)
    ) -> dict[str, Any]:
        project_name, prefix = project_and_agent(member, request.project_id)
        try:
            prepared = prepare_diagnosis(
                project_name, request.symptom, request.focus_areas, request.analysis_depth,
                [e.model_dump() for e in request.evidence],
            )
        except EvidenceRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

        prior: list[dict[str, Any]] = []
        if request.use_super_brain:
            # The query is only the researcher's own symptom text, never the evidence.
            with closing(db.shared()) as conn:
                allowed = context_project_ids(conn, request.project_id)
                matches = search_findings(conn, member["id"], prepared.symptom, allowed, limit=MAX_CONTEXT_FINDINGS)
            prepared.known_context, prepared.context_finding_ids = known_context_from(matches)
            prior = [
                {k: f[k] for k in ("id", "project_id", "title", "verification_status", "contributed_by_name")}
                for f in matches if f["id"] in prepared.context_finding_ids
            ]

        prepared.project_context = context_text(PrivateMemory(db, member["id"]).project_context(request.project_id))
        prepared.agent_rules = PrivateMemory(db, member["id"]).agent_rules(request.project_id)

        job = store.create(member["id"], request.project_id, prior)
        background.add_task(
            run_job, store, job.job_id, lambda: run_diagnosis(factory(prefix), prepared),
            lambda diagnosis: record_diagnosis(
                db, member["id"], request.project_id, prepared.symptom, diagnosis, prepared.context_finding_ids
            ),
        )
        return {
            "job_id": job.job_id,
            "status": job.status,
            "evidence_sent": [{"path": e["path"], "chars": len(e["excerpt"])} for e in prepared.evidence],
            "redactions": prepared.redactions,
            "prior_findings": prior,
            "used_project_overview": bool(prepared.project_context),
        }

    @app.post("/overview", status_code=202)
    def start_overview(
        request: OverviewIn, background: BackgroundTasks, member: dict[str, Any] = Depends(current_member)
    ) -> dict[str, Any]:
        """Your agent explains the project from a map you approved; saved privately when done."""
        project_name, prefix = project_and_agent(member, request.project_id)
        try:
            prepared = prepare_overview(project_name, request.tree, [e.model_dump() for e in request.excerpts],
                                        request.files, request.stats)
        except EvidenceRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

        prepared.agent_rules = PrivateMemory(db, member["id"]).agent_rules(request.project_id)

        def save(result: dict[str, Any]) -> None:
            PrivateMemory(db, member["id"]).save_project_context(
                request.project_id, result["overview"], result["reply"], prepared.stats, result["run_id"])

        job = store.create(member["id"], request.project_id, kind="overview")
        background.add_task(run_job, store, job.job_id, lambda: run_overview(factory(prefix), prepared), save)
        return {"job_id": job.job_id, "status": job.status, "chars_sent": len(prepared.creao_inputs()["observed_symptom"]),
                "redactions": prepared.redactions}

    @app.get("/projects/{project_id}/agent-rules")
    def agent_rules(project_id: str, member: dict[str, Any] = Depends(current_member)) -> dict:
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
        return {"instructions": PrivateMemory(db, member["id"]).agent_rules(project_id),
                "default_instructions": DEFAULT_AGENT_RULES}

    @app.put("/projects/{project_id}/agent-rules")
    def save_agent_rules(project_id: str, body: AgentRulesIn, member: dict[str, Any] = Depends(current_member)) -> dict:
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
        instructions, _ = redact_secrets(body.instructions.strip() or DEFAULT_AGENT_RULES)
        PrivateMemory(db, member["id"]).set_agent_rules(project_id, instructions)
        return {"instructions": instructions, "default_instructions": DEFAULT_AGENT_RULES}

    def readable_chat(memory: PrivateMemory, project_id: str, scope: str, member_id: str) -> list[dict]:
        # If project access is later removed, old all-project replies containing
        # that project's knowledge must not be displayed or sent back to CREAO.
        with closing(db.shared()) as conn:
            allowed = {p["id"] for p in projects_for_member(conn, member_id)}
        return [x for x in memory.chat_history(project_id, scope)
                if all(pid in allowed for pid in x.get("context_project_ids", []))
                and all(source["project_id"] in allowed for source in x.get("sources", []))]

    @app.get("/projects/{project_id}/chat")
    def chat_history(project_id: str, scope: Literal["project", "all"] = "project",
                     member: dict[str, Any] = Depends(current_member)) -> dict:
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
        pending = store.running_chat(member["id"], project_id)
        return {"exchanges": readable_chat(PrivateMemory(db, member["id"]), project_id, scope, member["id"]),
                "pending_job_id": pending["job_id"] if pending and pending["scope"] == scope else None}

    @app.delete("/projects/{project_id}/chat")
    def clear_chat(project_id: str, scope: Literal["project", "all"] = "project",
                   member: dict[str, Any] = Depends(current_member)) -> dict:
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
        with chat_lock:
            if store.running_chat(member["id"], project_id):
                raise HTTPException(status_code=409, detail="Wait for the current reply before clearing the conversation.")
            PrivateMemory(db, member["id"]).clear_chat(project_id, scope)
        return {"exchanges": []}

    @app.post("/superbrain/chat", status_code=202)
    @app.post("/agent/chat", status_code=202)
    def start_chat(body: ChatIn, background: BackgroundTasks, member: dict[str, Any] = Depends(current_member)) -> dict:
        """One answer from the central CREAO hub, grounded in shared agent knowledge."""
        started = time.perf_counter()
        message, _ = redact_secrets(body.message.strip())
        if not message:
            raise HTTPException(status_code=422, detail="Enter a message first.")
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], body.project_id)
            project_name = conn.execute("SELECT name FROM projects WHERE id = ?", (body.project_id,)).fetchone()[0]
            allowed = [p["id"] for p in projects_for_member(conn, member["id"])]
        project_ids = allowed if body.scope == "all" else [body.project_id]
        scope_name = "all projects you can access" if body.scope == "all" else project_name
        memory = PrivateMemory(db, member["id"])
        with chat_lock:
            if store.running_chat(member["id"], body.project_id):
                raise HTTPException(status_code=409, detail="Your Super Brain reply is still running in this project.")
            history = readable_chat(memory, body.project_id, body.scope, member["id"])
            rules = memory.agent_rules(body.project_id)
            command = rule_command(message)
            instructions, instruction_action = None, None
            if command:
                instruction_action, text = command
                if instruction_action != "show":
                    instructions = (rules + "\n" + text) if instruction_action == "append" else text
                    if len(instructions) > MAX_RULE_CHARS:
                        raise HTTPException(status_code=422, detail="Instructions are too long. Edit them in Agent instructions (2,000 characters maximum).")
                    memory.set_agent_rules(body.project_id, instructions)
                    rules = instructions
            previous = history[-1].get("sources", []) if history else []
            with closing(db.shared()) as conn:
                matches, stats = select_chat_findings(conn, member["id"], message, project_ids, previous)
            findings = knowledge_context(matches, stats)
            included = {f["id"] for f in findings["selected_findings"]}
            prior = [{k: f[k] for k in ("id", "project_id", "title", "verification_status", "contributed_by_name")}
                     for f in matches if f["id"] in included]
            job = store.create(member["id"], body.project_id, prior, kind="chat", scope=body.scope)
        settings = chat_choice()

        def task() -> dict:
            # A and B keep their personal agents for analysis. Super Brain chat runs on the
            # dedicated chat agent once it is set up, otherwise on the central hub.
            if client_factory:
                client = client_factory(settings.slot)
            else:
                config = chat_agent() if settings.slot == "CHAT" else hub_agent()
                client = CreaoClient(config, poll_interval=1.0, chat_model_id=settings.model_id)
            result = run_chat(client, scope_name, message, rules, history, findings, instruction_action,
                              dedicated=settings.slot == "CHAT")
            return {**result, "agent_slot": settings.slot, "model": settings.model_id or "saved",
                    "knowledge_stats": stats, "context_project_ids": project_ids,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000)}

        def save(result: dict) -> None:
            memory.save_chat_exchange(body.project_id, message, {**result, "sources": prior}, body.scope)

        background.add_task(run_job, store, job.job_id, task, save)
        return {"job_id": job.job_id, "status": job.status, "prior_findings": prior,
                "knowledge_stats": stats, "instructions": instructions, "agent_slot": settings.slot}

    @app.get("/projects/{project_id}/overview")
    def project_overview(project_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """Your saved overview of a project. Only you can see it."""
        try:
            context = PrivateMemory(db, member["id"]).project_context(project_id)
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are not a member of that project.") from None
        if context is None:
            raise HTTPException(status_code=404, detail="No overview of this project yet.")
        return context

    @app.post("/projects", status_code=201)
    def add_project(request: ProjectIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """Create a project in your group and choose its members (leader only)."""
        require_leader(member)
        with closing(db.shared()) as conn:
            try:
                return create_project(conn, member["workspace_id"], request.id, request.name, request.description,
                                      [member["id"], *request.member_ids])
            except InvalidProject as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/jobs/{job_id}")
    @app.get("/diagnose/{job_id}")
    def get_diagnosis(job_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None or job["member_id"] != member["id"]:  # other members' jobs look like they don't exist
            raise HTTPException(status_code=404, detail="No diagnosis job with that ID.")
        if job["kind"] == "chat":
            with closing(db.shared()) as conn:
                allowed = {p["id"] for p in projects_for_member(conn, member["id"])}
            used = {source["project_id"] for source in job["prior_findings"]}
            used.update((job.get("diagnosis") or {}).get("context_project_ids", []))
            if job["project_id"] not in allowed or not used <= allowed:
                raise HTTPException(status_code=404, detail="No chat job with that ID.")
        return job

    @app.get("/drafts")
    def my_drafts(member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Your pending drafts. Only you can see them."""
        return PrivateMemory(db, member["id"]).drafts()

    @app.post("/drafts/{draft_id}/approve")
    def approve(draft_id: str, edits: ApproveIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        try:
            return approve_draft(db, member["id"], draft_id, edits.model_dump())
        except DraftNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except DraftAlreadyDecided as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except InvalidDraft as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are no longer a member of that project.") from None

    @app.post("/drafts/{draft_id}/reject")
    def reject(draft_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, str]:
        try:
            reject_draft(db, member["id"], draft_id)
        except DraftNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except DraftAlreadyDecided as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are no longer a member of that project.") from None
        return {"status": "rejected", "detail": "The draft stays private; nothing was shared."}

    @app.get("/projects/{project_id}/findings")
    def project_findings(project_id: str, member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Approved findings in one project of the Super Brain."""
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
            return list_project_findings(conn, project_id)

    @app.get("/superbrain/findings")
    def super_brain_findings(
        project_id: str | None = None, member: dict[str, Any] = Depends(current_member)
    ) -> list[dict[str, Any]]:
        """Approved findings across every project you belong to, newest first."""
        with closing(db.shared()) as conn:
            if project_id:
                require_member_of(conn, member["id"], project_id)
            return list_readable_findings(conn, member["id"], [project_id] if project_id else None)

    @app.get("/superbrain/search")
    def ask_super_brain(
        q: str = Query(min_length=2, max_length=500),
        project_id: str | None = None,
        member: dict[str, Any] = Depends(current_member),
    ) -> list[dict[str, Any]]:
        """Search approved findings in the projects you belong to."""
        with closing(db.shared()) as conn:
            if project_id:
                require_member_of(conn, member["id"], project_id)
            return search_findings(conn, member["id"], q, [project_id] if project_id else None)

    @app.get("/findings/{finding_id}")
    def finding_detail(finding_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """One finding and the findings linked to it that you may read."""
        with closing(db.shared()) as conn:
            finding = get_readable_finding(conn, member["id"], finding_id)
            if finding is None:  # findings in projects you can't read look like they don't exist
                raise HTTPException(status_code=404, detail="No finding with that ID.")
            return {"finding": finding, "related": related_findings(conn, member["id"], finding_id)}

    @app.get("/policy")
    def policy() -> dict[str, str]:
        """Membership policies disclosed to every member in advance."""
        return {"offboarding": OFFBOARDING_POLICY}

    @app.get("/settings")
    def my_settings(member: dict[str, Any] = Depends(current_member)) -> dict[str, bool]:
        return {"save_alert_history": PrivateMemory(db, member["id"]).alert_history_enabled()}

    @app.put("/settings")
    def update_settings(body: SettingsIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, bool]:
        PrivateMemory(db, member["id"]).set_alert_history(body.save_alert_history)
        return {"save_alert_history": body.save_alert_history}

    @app.post("/alerts")
    def post_alerts(body: AlertsIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """From the researcher's local watcher. Kept in memory for the session; also saved to
        their private memory only if they turned alert history on (off by default)."""
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], body.project_id)
        try:
            records = alert_store.add(member["id"], body.project_id, body.run_label, [a.model_dump() for a in body.alerts])
        except InvalidAlert as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        memory = PrivateMemory(db, member["id"])
        saved = memory.alert_history_enabled()
        if saved:
            memory.save_alerts(body.project_id, [asdict(r) for r in records])
        return {"stored": len(records), "saved": saved}

    @app.get("/alerts/history")
    def saved_alerts(member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Your saved alert history (only exists if you turned it on)."""
        return PrivateMemory(db, member["id"]).saved_alerts()

    @app.delete("/alerts/history")
    def delete_saved_alerts(member: dict[str, Any] = Depends(current_member)) -> dict[str, int]:
        return {"deleted": PrivateMemory(db, member["id"]).delete_saved_alerts()}

    def require_leader(member: dict[str, Any]) -> None:
        if member["role"] != "leader":
            raise HTTPException(status_code=403, detail="Only the group leader can do this.")

    @app.get("/members")
    def group_members(member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Everyone in the group, including offboarded members (leader only)."""
        require_leader(member)
        with closing(db.shared()) as conn:
            return [m for m in list_members(conn, active_only=False) if m["workspace_id"] == member["workspace_id"]]

    @app.post("/offboarding")
    def offboard(body: OffboardIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """Hand a leaving member's eligible work to the Super Brain, verify it, retire their agent."""
        try:
            return offboard_member(db, member["id"], body.member_id)
        except OffboardingError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    @app.get("/offboarding")
    def offboarding_history(member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        require_leader(member)
        with closing(db.shared()) as conn:
            return handoff_history(conn, member["workspace_id"])

    @app.get("/alerts")
    def get_alerts(project_id: str | None = None, member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Your own alerts from this session, newest first."""
        if project_id:
            with closing(db.shared()) as conn:
                require_member_of(conn, member["id"], project_id)
        return alert_store.for_member(member["id"], project_id)

    @app.delete("/alerts")
    def clear_alerts(member: dict[str, Any] = Depends(current_member)) -> dict[str, str]:
        alert_store.clear(member["id"])
        return {"status": "cleared"}

    @app.post("/simulation-runs", status_code=201)
    def share_run(body: RunIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """Put a run you started watching on the dashboard (sent by your local connector)."""
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], body.project_id)
            return create_run(conn, member, body.project_id, body.run_label)

    @app.post("/simulation-runs/{run_id}/report")
    def report_run(run_id: str, body: RunReportIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, int]:
        """New step numbers and alerts for a run you watch. Only the researcher watching it can report."""
        with closing(db.shared()) as conn:
            run = get_visible_run(conn, member, run_id)
            if run is None or run["member_id"] != member["id"]:
                raise HTTPException(status_code=404, detail="No run with that ID.")
            require_member_of(conn, member["id"], run["project_id"])
            try:
                points = [p.model_dump() for p in body.points]
                return add_report(conn, member["id"], run_id, points, [a.model_dump() for a in body.alerts], body.stopped)
            except RunNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except InvalidAlert as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/simulation-runs")
    def shared_runs(project_id: str | None = None, member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Runs you may see, newest first: your own and, for the group leader, every run
        in their projects. Other researchers' runs never appear."""
        with closing(db.shared()) as conn:
            if project_id:
                require_member_of(conn, member["id"], project_id)
            return visible_runs(conn, member, [project_id] if project_id else None)

    @app.get("/simulation-runs/alerts")
    def shared_run_alerts(
        limit: int = Query(default=20, ge=1, le=100), member: dict[str, Any] = Depends(current_member)
    ) -> list[dict[str, Any]]:
        """The newest alerts from runs you may see."""
        with closing(db.shared()) as conn:
            return recent_alerts(conn, member, limit)

    @app.get("/simulation-runs/{run_id}")
    def shared_run(run_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        """One run: its numbers per metric, for graphs, and its alerts."""
        with closing(db.shared()) as conn:
            detail = run_detail(conn, member, run_id)
        if detail is None:  # runs you can't see look like they don't exist
            raise HTTPException(status_code=404, detail="No run with that ID.")
        return detail

    return app


app = create_app()
