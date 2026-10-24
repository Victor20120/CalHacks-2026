# Changelog

One entry per commit, newest first. Each commit adds its own entry with the hash set to `pending`; the next commit replaces `pending` with the real hash.

---

## 2026-10-23 · `pending` · Add approval path from private diagnosis to shared finding

**Why:** Build plan step 4: a diagnosis is saved privately with a draft finding, and only the researcher's explicit approval shares it to the Super Brain.

**Changed**
- `/diagnose` now requires sign-in and project membership, runs on the researcher's own personal agent (`AGENT_1` / `AGENT_2`), and only the researcher can read their jobs. Requests use `project_id`, `focus_areas` (list) and `analysis_depth`.
- Finished diagnoses are saved to the researcher's private memory as a hypothesis episode, with a draft built from the agent's top issue (symptom, likely cause, evidence, how to validate), always labelled `hypothesis`.
- Added `backend/knowledge_sharing/publishing.py`: approve (with optional edits; approval and finding written in one transaction, secrets blanked, never published twice) and reject (nothing written to the shared database).
- Added `backend/memory/shared_findings.py` and endpoints `GET /drafts`, `POST /drafts/{id}/approve`, `POST /drafts/{id}/reject`, `GET /projects/{id}/findings`.
- Database: private `share_drafts` table, `approvals.draft_id`, and `SCHEMA_VERSION` so old database files are refused with reset instructions.
- Local connector: `diagnose --as --project --focus --depth`, plus `drafts`, `approve`, `reject` and `findings` commands.
- Added `tests/test_knowledge_sharing.py`; rewrote `tests/test_diagnose_api.py` for the signed-in, per-researcher flow.
- Updated `README.md` (full A → approve → B flow), `backend/README.md` and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 131 passed, 1 skipped (symlink test on Windows).
- Real run `915f1428` on Researcher A's agent through the connector: draft created privately; Researcher B saw no findings before approval, saw the finding (still `hypothesis`, with provenance) after, and was refused on the wave project.
- The old local database was refused with reset instructions, then reset.
- Secret scan: no API key or app ID in the commit; `backend/.env` and `backend/data/` not included.

---

## 2026-10-23 · `524e7a1` · Add databases, demo workspace, sign-in and personal agents

**Why:** Build plan step 3 (SQLite models and demo data), plus the start of step 4: each researcher gets their own CREAO agent, so CREAO's own agent memory can't carry one researcher's evidence into another's runs.

**Changed**
- Added `backend/db/`: the shared Super Brain (`superbrain.db`) and one private memory file per researcher (`private/<member>.db`), with separate tables so no shared query can return private memory. `approvals` stores no draft text.
- Added `backend/workspaces/`: membership lookups (offboarded members have no project access) and the demo workspace: a group leader, Researcher A (both projects) and Researcher B (`heat-diffusion` only). Reset with `python -m backend.workspaces.demo --reset`.
- Added `backend/memory/private_memory.py`: private episodes split by project, with a membership check on every read and write.
- Added `backend/auth/`: prototype sign-in (`GET /demo/members`, `POST /sign-in`, `GET /me`) and project permission checks. Offboarding takes effect on the next request.
- CREAO adapter: `personal_agent()` for the researchers' agents (four required inputs, fixed choices checked before sending, `focus_areas` as comma-separated text, issue-list request). `analysis_ran` detection fixed: it now uses the `analysis_depth` the agent echoes back, because agents return zero counts and even a numeric score of 0 on runs that analyzed nothing.
- Added fixtures from real runs: `creao_agent2_run_not_analyzed.json`, `creao_personal_agent_contract_probe.json`, `creao_personal_agent_diagnosis.json`.
- Added `tests/test_database.py` and `tests/conftest.py` (every test uses temporary data folders), and new adapter tests.
- `.gitignore`: ignores `backend/data/`.
- Updated `backend/README.md` (databases, sign-in, agent input contracts, key behaviour) and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 105 passed, 1 skipped (symlink test on Windows).
- Demo database deleted, recreated and reloaded twice with the real command: same counts each time.
- Probe runs `8eb4dc7d` and `7bcbb28f`: both personal agents reachable, with identical input contracts.
- Real diagnosis `aaa9f564` on `AGENT_1`: comma-separated `focus_areas` accepted, 10 issues returned matching the summary counts, root cause found.
- Found: CREAO `capi_` keys can read other agents' runs, and listing runs returns 200 for unknown app IDs. Documented in `backend/README.md`.
- Secret scan: no API key or app ID in the commit; `backend/.env` and `backend/data/` not included.

---

## 2026-10-23 · `40e192e` · Add diagnosis endpoint, local connector and evidence rules

**Why:** Build plan step 2: a researcher can grant a project folder, pick the files that matter, and get a diagnosis, while secrets, sensitive files and local paths never leave the computer.

**Changed**
- Added `backend/orchestration/evidence_policy.py`: rules shared by the connector and the backend. Size limits (10 excerpts, 4,000 characters each, 12,000 total), relative paths only, sensitive files never sent (`.env`, private keys, credential files, `.git`, `.ssh`, …), and secrets inside files blanked out.
- Added `local_connector/file_access/`: folder grants stored in `~/.simguard/grants.json`. Reads are refused outside granted folders, for sensitive files and for binary files.
- Added `local_connector/evidence/`: keeps only the useful parts of the selected files (log head, lines around anomalies, log tail, or a chosen line range of code).
- Added `local_connector/__main__.py`: the command-line connector (`grant`, `list`, `revoke`, `diagnose`). `diagnose` shows exactly what will be sent and asks first.
- Added `backend/api/app.py` (FastAPI): `GET /health`, `POST /diagnose` (returns a job ID) and `GET /diagnose/{job_id}`. Added `backend/orchestration/diagnosis.py` and `jobs.py`: the backend re-checks all evidence before calling CREAO, and keeps jobs in memory for now.
- Added `tests/fixtures/sample_project/`: a small explicit heat solver with an unstable time step, and the real log it produces.
- Added `tests/test_file_access.py` and `tests/test_diagnose_api.py`.
- `backend/requirements.txt`: added fastapi, uvicorn and httpx.
- Updated `README.md` ("Try a diagnosis"), `backend/README.md` (endpoints and evidence rules) and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 72 passed, 1 skipped (the symlink test needs extra permissions on Windows).
- Real run `e191c825` through the connector and backend: with only the symptom given, the agent found `r = 0.30 > 0.25` from the config and code.
- The connector refused to read `.env`.
- Secret scan: no API key or app ID in the commit; `backend/.env` not included.

---

## 2026-10-23 · `d679567` · Add CREAO adapter with issue list, tests and changelog

**Why:** Build plan step 1: one tested module for every CREAO call, and a way for the dashboard to get a list of issues instead of only prose.

**Changed**
- Added `backend/creao/` (`client.py`, `__init__.py`): the CREAO adapter. It checks required inputs before sending, starts runs (never retried, since each start costs credits), polls until done, keeps waiting through rate limits, gives clear errors (`CreaoInputError`, `CreaoRequestError`, `CreaoRunFailed`, `CreaoTimeout`) and never exposes the key.
- Hub runs now send an extra `output_format` input asking the agent to end its reply with a JSON issue list. The adapter moves it into `result.issues` and treats anything not labelled exactly `confirmed` as `suspected`.
- Added `tests/test_creao_client.py` (24 tests against a fake CREAO) and `pytest.ini`.
- Added fixtures `creao_hub_run_missing_inputs.json` (from run history, no credits) and `creao_hub_run_with_issue_list.json`.
- Added `backend/requirements.txt` (requests, python-dotenv, pytest).
- Added this `CHANGELOG.md`, updated with every commit.
- Updated `README.md`, `backend/README.md`, `Documents/build-plan.md` and `Documents/dashboard-design.md`.

**Checks**
- `python -m pytest`: 24 passed, no network or credits.
- Real run `790fe5fd` through the adapter: completed, 6 issues returned (2 confirmed, 4 suspected), matching the agent's summary counts.
- Adapter read back both earlier real runs correctly (status checks, no credits).
- Secret scan: no API key or app ID in the commit; `backend/.env` not included.
- Not done: credits per run still need to be read in the CREAO app.

---

## 2026-10-23 · `c27bea3` · Adopt SimGuard product decisions and verify CREAO hub agent

**Why:** The SimGuard product decisions were settled, so the repo and docs were brought in line with them, and step 1 of the build plan (verify CREAO) was started.

**Changed**
- Restructured folders to the SimGuard layout: `backend/` (`api`, `auth`, `workspaces`, `creao`, `orchestration`, `memory`, `monitoring`, `knowledge_sharing`, `db`), `local_connector/` (`file_access`, `evidence`, `simulation_monitor`) and `tests/`. Removed `agents/`, `shared/`, `infra/`, `scripts/`, `sample-data/` and the empty frontend page folders.
- Rewrote `Documents/build-plan.md` around the 7 steps of the first milestone, each with a checkpoint.
- Renamed `Documents/website-architecture.md` to `Documents/dashboard-design.md` and narrowed it to the dashboard UI.
- Added `backend/testcreo.py` (working CREAO smoke test) and `backend/.env.example`.
- Added `tests/fixtures/creao_hub_run_completed.json`: a real known-good hub agent run (app ID redacted), for use as the mock CREAO.
- `.gitignore`: now ignores SQLite databases and `.pytest_cache/`, and keeps `.env.example` tracked.
- Rewrote `README.md` and `backend/README.md` (adds the verified CREAO API contract).

**Checks**
- Hub agent run with all three inputs completed in about 60 s and correctly identified the time-step stability violation.
- Secret scan of the commit: no API key or app ID; `backend/.env` not included.

---

## 2026-10-23 · `986e2c3` · Set up project structure and website architecture skeleton

**Why:** First commit: an organized starting structure before any code.

**Changed**
- Added the initial folder layout (`frontend/`, `backend/`, `agents/`, `shared/`, `infra/`, `scripts/`, `sample-data/`), each with a README. Replaced in `c27bea3`.
- Added `Documents/website-architecture.md`: a desktop-first website architecture skeleton.
- Added `README.md` and a `.gitignore` for secrets and build output.

**Checks**
- None (documentation and empty folders only).
