"""Database tables.

The shared Super Brain and each researcher's private memory are separate
database files with separate tables, so a shared query cannot return private
memory. Rejected drafts and draft text never go into the shared database:
`approvals` only records that an approved finding was approved, by whom.
"""

# Bump when an existing table changes. An existing database file with another
# version is refused with instructions to reset it (prototype: there are no
# migrations). New tables don't need a bump: CREATE TABLE IF NOT EXISTS adds them
# to existing files.
SCHEMA_VERSION = 2

FINDING_CATEGORIES = ("research_result", "code_change", "error_diagnosis")
VERIFICATION_STATUSES = ("hypothesis", "supported", "verified", "unresolved")

SHARED_TABLES = (
    "workspaces",
    "members",
    "projects",
    "project_members",
    "agents",
    "approvals",
    "shared_findings",
    "finding_links",
    "handoff_events",
    "handoff_records",
    "simulation_runs",
    "run_points",
    "run_alerts",
    "auto_shares",
    "finding_notes",
    "worksheets",
    "worksheet_items",
    "finding_runs",
    "finding_comments",
    "worksheet_weeks",
    "worksheet_in_week",
)
PRIVATE_TABLES = ("runs", "private_episodes", "share_drafts", "settings", "alerts", "project_context")

SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('leader', 'researcher')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'offboarded')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id  TEXT NOT NULL REFERENCES projects(id),
    member_id   TEXT NOT NULL REFERENCES members(id),
    added_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, member_id)
);

-- One personal agent per member. The CREAO key and app ID stay in backend/.env;
-- this only names which CREAO_<prefix>_KEY / CREAO_<prefix>_APP_ID to use.
CREATE TABLE IF NOT EXISTS agents (
    id                TEXT PRIMARY KEY,
    member_id         TEXT NOT NULL UNIQUE REFERENCES members(id),
    creao_env_prefix  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at        TEXT NOT NULL
);

-- draft_id names the researcher's private draft that was approved (an ID only, no
-- text); UNIQUE so the same draft can never be published twice.
CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    project_id    TEXT NOT NULL REFERENCES projects(id),
    member_id     TEXT NOT NULL REFERENCES members(id),
    draft_id      TEXT NOT NULL UNIQUE,
    edited        INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0, 1)),
    approved_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_findings (
    id                   TEXT PRIMARY KEY,
    workspace_id         TEXT NOT NULL REFERENCES workspaces(id),
    project_id           TEXT NOT NULL REFERENCES projects(id),
    category             TEXT NOT NULL CHECK (category IN ('research_result', 'code_change', 'error_diagnosis')),
    title                TEXT NOT NULL,
    summary              TEXT NOT NULL,
    verification_status  TEXT NOT NULL CHECK (verification_status IN ('hypothesis', 'supported', 'verified', 'unresolved')),
    contributed_by       TEXT NOT NULL REFERENCES members(id),
    approval_id          TEXT NOT NULL UNIQUE REFERENCES approvals(id),
    source_creao_run_id  TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_links (
    from_finding_id  TEXT NOT NULL REFERENCES shared_findings(id),
    to_finding_id    TEXT NOT NULL REFERENCES shared_findings(id),
    relation         TEXT NOT NULL CHECK (relation IN (
                         'occurred_in', 'associated_with', 'caused_by', 'tested_with',
                         'resolved_by', 'contradicts', 'similar_to')),
    created_at       TEXT NOT NULL,
    PRIMARY KEY (from_finding_id, to_finding_id, relation)
);

-- Offboarding: one row per attempt to hand a leaving member's work to the Super Brain.
CREATE TABLE IF NOT EXISTS handoff_events (
    id                   TEXT PRIMARY KEY,
    workspace_id         TEXT NOT NULL REFERENCES workspaces(id),
    member_id            TEXT NOT NULL REFERENCES members(id),
    initiated_by         TEXT NOT NULL REFERENCES members(id),
    status               TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    records_prepared     INTEGER NOT NULL DEFAULT 0,
    records_transferred  INTEGER NOT NULL DEFAULT 0,
    error                TEXT,
    started_at           TEXT NOT NULL,
    finished_at          TEXT
);

-- Which shared findings came from a handoff (and so were not individually approved).
CREATE TABLE IF NOT EXISTS handoff_records (
    handoff_id  TEXT NOT NULL REFERENCES handoff_events(id),
    finding_id  TEXT NOT NULL UNIQUE REFERENCES shared_findings(id),
    source      TEXT NOT NULL CHECK (source IN ('episode', 'draft')),
    PRIMARY KEY (handoff_id, finding_id)
);

-- Findings a researcher's personal agent shared by itself from a chat (the group's
-- choice, 2026-10-24): not approved one by one, always a hypothesis, marked as such.
-- agent_guess keeps the agent's own interpretation apart from what was established.
CREATE TABLE IF NOT EXISTS auto_shares (
    finding_id   TEXT PRIMARY KEY REFERENCES shared_findings(id),
    source       TEXT NOT NULL CHECK (source IN ('chat')),
    agent_guess  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

-- More detail, a member's own guess, or a correction added to a shared finding.
CREATE TABLE IF NOT EXISTS finding_notes (
    id          TEXT PRIMARY KEY,
    finding_id  TEXT NOT NULL REFERENCES shared_findings(id),
    member_id   TEXT NOT NULL REFERENCES members(id),
    kind        TEXT NOT NULL CHECK (kind IN ('detail', 'guess', 'correction')),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Feedback on a shared finding, between the leader and the researchers in its project.
-- Unlike notes, comments are a conversation between people; agents don't read them.
CREATE TABLE IF NOT EXISTS finding_comments (
    id          TEXT PRIMARY KEY,
    finding_id  TEXT NOT NULL REFERENCES shared_findings(id),
    member_id   TEXT NOT NULL REFERENCES members(id),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- The run a finding came from, attached by the researcher when sharing it (or by their
-- agent, for the run it looked at). Its graph shows with the finding to whoever may see the run.
CREATE TABLE IF NOT EXISTS finding_runs (
    finding_id   TEXT PRIMARY KEY REFERENCES shared_findings(id),
    run_id       TEXT NOT NULL REFERENCES simulation_runs(id),
    attached_at  TEXT NOT NULL
);

-- The Super Brain's teacher: a worksheet per researcher, made from what the other
-- researchers in the project shared. The researcher answers; the leader comments.
CREATE TABLE IF NOT EXISTS worksheets (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(id),
    project_id      TEXT NOT NULL REFERENCES projects(id),
    member_id       TEXT NOT NULL REFERENCES members(id),
    created_by      TEXT NOT NULL REFERENCES members(id),
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'submitted', 'reviewed')),
    leader_comment  TEXT NOT NULL DEFAULT '',
    creao_run_id    TEXT,
    created_at      TEXT NOT NULL,
    submitted_at    TEXT,
    reviewed_at     TEXT
);

-- A question or a hands-on "try it" step, the findings it teaches, and its answers.
CREATE TABLE IF NOT EXISTS worksheet_items (
    id              TEXT PRIMARY KEY,
    worksheet_id    TEXT NOT NULL REFERENCES worksheets(id),
    position        INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('question', 'try')),
    topic           TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    model_answer    TEXT NOT NULL DEFAULT '',
    finding_ids     TEXT NOT NULL DEFAULT '[]',
    answer          TEXT NOT NULL DEFAULT '',
    leader_comment  TEXT NOT NULL DEFAULT ''
);

-- Worksheets come in weeks: each "Make worksheets" is its project's next week, and teaches
-- what's new to each researcher since their last worksheet (unless only_new is 0). The leader
-- can cancel a whole week, or one person's worksheet in it, and restore either.
CREATE TABLE IF NOT EXISTS worksheet_weeks (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id),
    week          INTEGER NOT NULL,  -- the order made in; the number shown skips cancelled weeks
    made_by       TEXT NOT NULL REFERENCES members(id),
    made_at       TEXT NOT NULL,
    only_new      INTEGER NOT NULL DEFAULT 1 CHECK (only_new IN (0, 1)),
    cancelled_at  TEXT,
    UNIQUE (project_id, week)
);

CREATE TABLE IF NOT EXISTS worksheet_in_week (
    worksheet_id  TEXT PRIMARY KEY REFERENCES worksheets(id),
    week_id       TEXT NOT NULL REFERENCES worksheet_weeks(id),
    cancelled_at  TEXT  -- this one person's worksheet was cancelled
);

-- Runs watched on a researcher's computer, seen by them and the group leader.
-- Only the numbers read from the log and its alerts are kept, never the log file.
CREATE TABLE IF NOT EXISTS simulation_runs (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    project_id    TEXT NOT NULL REFERENCES projects(id),
    member_id     TEXT NOT NULL REFERENCES members(id),
    run_label     TEXT NOT NULL,
    watching      INTEGER NOT NULL DEFAULT 1 CHECK (watching IN (0, 1)),
    last_step     INTEGER,
    started_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- One number per metric per step. NULL means the log reported NaN or Inf there.
CREATE TABLE IF NOT EXISTS run_points (
    run_id  TEXT NOT NULL REFERENCES simulation_runs(id),
    metric  TEXT NOT NULL,
    step    INTEGER NOT NULL,
    value   REAL
);
CREATE INDEX IF NOT EXISTS run_points_by_metric ON run_points (run_id, metric, step);

CREATE TABLE IF NOT EXISTS run_alerts (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES simulation_runs(id),
    severity     TEXT NOT NULL CHECK (severity IN ('informational', 'warning', 'critical')),
    check_name   TEXT NOT NULL,
    message      TEXT NOT NULL,
    metric       TEXT,
    step         INTEGER,
    line_number  INTEGER,
    line         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
"""

PRIVATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    label       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('running', 'finished', 'crashed', 'unknown')),
    started_at  TEXT,
    notes       TEXT NOT NULL DEFAULT ''
);

-- "status" keeps attempted explanations apart from confirmed causes.
CREATE TABLE IF NOT EXISTS private_episodes (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('incident', 'debugging_attempt', 'test', 'diagnosis', 'note')),
    summary       TEXT NOT NULL,
    details       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'hypothesis', 'confirmed', 'resolved', 'unresolved')),
    run_id        TEXT REFERENCES runs(id),
    creao_run_id  TEXT,
    created_at    TEXT NOT NULL
);

-- Proposed contributions to the Super Brain. They stay in this private file until
-- the researcher approves one; rejected drafts never leave it.
CREATE TABLE IF NOT EXISTS share_drafts (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    category             TEXT NOT NULL CHECK (category IN ('research_result', 'code_change', 'error_diagnosis')),
    title                TEXT NOT NULL,
    summary              TEXT NOT NULL,
    verification_status  TEXT NOT NULL CHECK (verification_status IN ('hypothesis', 'supported', 'verified', 'unresolved')),
    source_creao_run_id  TEXT,
    episode_id           TEXT REFERENCES private_episodes(id),
    status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    finding_id           TEXT,
    created_at           TEXT NOT NULL,
    decided_at           TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Monitoring alerts, saved only while the researcher has alert history turned on
-- (off by default). Never in the shared database.
CREATE TABLE IF NOT EXISTS alerts (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    run_label    TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('informational', 'warning', 'critical')),
    check_name   TEXT NOT NULL,
    message      TEXT NOT NULL,
    metric       TEXT,
    line_number  INTEGER,
    line         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

-- "What is this project?": the researcher's saved overview of each project,
-- written by their agent from a project map they approved. Private.
CREATE TABLE IF NOT EXISTS project_context (
    project_id    TEXT PRIMARY KEY,
    overview      TEXT NOT NULL,  -- JSON: purpose, structure, how_to_run, key_parameters, open_questions
    reply         TEXT NOT NULL,  -- the agent's prose answer
    map_stats     TEXT NOT NULL,  -- JSON: file counts of the map it was built from
    creao_run_id  TEXT,
    updated_at    TEXT NOT NULL
);
"""
