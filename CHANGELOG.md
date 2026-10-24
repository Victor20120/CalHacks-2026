# Changelog

One entry per commit, newest first. Each commit adds its own entry with the hash set to `pending`; the next commit replaces `pending` with the real hash.

---

## 2026-10-23 · `pending` · Add diagnosis endpoint, local connector and evidence rules

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
