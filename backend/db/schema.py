"""Database tables.

The shared Super Brain and each researcher's private memory are separate
database files with separate tables, so a shared query cannot return private
memory. Rejected drafts and draft text never go into the shared database:
`approvals` only records that an approved finding was approved, by whom.
"""

# Bump when a table changes. An existing database file with another version is
# refused with instructions to reset it (prototype: there are no migrations).
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
)
PRIVATE_TABLES = ("runs", "private_episodes", "share_drafts")

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
"""
