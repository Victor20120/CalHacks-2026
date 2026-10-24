# Build Plan — First Milestone

**Status:** v0.2
**Milestone:** a single local simulation diagnosis reaches CREAO, is shown in the dashboard, and can become one researcher-approved group finding; a second researcher can retrieve that finding.

## How to use this plan

- Work top to bottom. Each step builds on the one before it.
- Don't start the next step until the current step's **checkpoint** passes.
- Tick boxes (`[x]`) as you go, and put your initials next to a task when you take it.
- Automated tests use a **mock CREAO**, so they cost no credits. Only the checks marked **(uses credits)** call the real API.
- If a checkpoint fails, fix it or write down the workaround before moving on.

---

## Progress

| # | Step | Main folders | Status |
|---|---|---|---|
| 0 | Setup | `backend/` | Mostly done |
| 1 | Verify CREAO with a known-good input | `backend/creao/`, `tests/` | Done except recording credits |
| 2 | Diagnosis endpoint with file permissions | `backend/api/`, `backend/orchestration/`, `local_connector/` | Done |
| 3 | SQLite models and demo data | `backend/db/`, `backend/workspaces/`, `backend/memory/`, `backend/auth/` | Done |
| 4 | Approval path to publish a finding | `backend/knowledge_sharing/` | Done |
| 5 | Scoped lookup for Researcher B | `backend/memory/`, `backend/auth/` | Done |
| 6 | Dashboard and local anomaly checks | `frontend/`, `local_connector/simulation_monitor/`, `backend/monitoring/` | Not started |
| 7 | Optional: alert history and handoff | `backend/monitoring/`, `backend/memory/` | Not started |

---

## Standing checks (before every push)

- [ ] All tests pass (`python -m pytest` from the repo root), using mock CREAO only
- [ ] `git status` shows no `.env`, `.db` files or other secrets about to be committed
- [ ] No code edits source files or stops a simulation without the researcher's approval
- [ ] Any dashboard page you changed looks right at 1280px and 1440px wide

---

## Step 0 — Setup

**Tasks**
- [x] CREAO API Trigger key (`capi_`) and app ID for the hub agent in `backend/.env`
- [x] `backend/.env.example` listing every variable name (no values)
- [ ] Every teammate installs Python and Node.js, clones the repo and creates their own `backend/.env`
- [x] `backend/requirements.txt` (requests, python-dotenv, pytest; FastAPI and uvicorn get added in step 2)
- [ ] Python virtual environment (optional; packages are currently installed in the global Python)

**Checkpoint 0**
- [x] `git check-ignore backend/.env` prints `backend/.env`
- [x] `backend/testcreo.py` starts a run and gets the result back (2026-10-23) **(uses credits)**
- [ ] Every teammate can run `testcreo.py` with their own `.env`

---

## Step 1 — Verify CREAO with a known-good input

**Tasks**
- [x] For each configured agent, write down the exact input fields it expects. Hub agent: `project_path`, `observed_symptom`, `focus_areas`, plus optional `analysis_depth` (recorded in `backend/README.md`)
- [x] Run one test with every field filled in, using pasted evidence text (e.g. a short log snippet showing a NaN), not a local path
- [x] Save that real request and response, with keys removed, in `tests/fixtures/` to use as the mock CREAO (`creao_hub_run_completed.json`)
- [x] Decide how the dashboard gets the itemized issue list. Decision: every hub run sends an extra `output_format` input asking the agent to end its reply with a JSON issue list, and the adapter moves it into `result.issues`. No change to the agent in CREAO needed
- [x] Move the working request from `testcreo.py` into a small adapter in `backend/creao/` (start run, poll, return result), and keep `testcreo.py` working (`backend/creao/client.py`; `testcreo.py` unchanged)

**Checkpoint 1**
- [x] The test run completes, and the answer addresses the symptom instead of reporting missing inputs **(uses credits)** — passed 2026-10-23: correctly found the time-step stability violation
- [ ] Record the credits a run uses. Someone has to read this in the CREAO app, because the `capi_` key can't see usage. Real runs to look up: `790fe5fd` (with issue list) and `dd8668fa`
- [x] Automated: the adapter returns the reply text from the saved mock response, with no network
- [x] Automated: the adapter raises a clear error for `failed`, `cancelled` and timeouts
- [x] The key never appears in logs or printed output (tested for error messages, logs and `repr`)
- [x] Manual: the adapter reads back both real runs from CREAO correctly (status checks only, no credits)
- [x] Manual: the adapter starts a real run and gets back 6 issues that match the agent's summary counts **(uses credits)** — passed 2026-10-23, run `790fe5fd`

---

## Step 2 — Diagnosis endpoint with file permissions

**Tasks**
- [x] FastAPI app in `backend/api/` with `GET /health`
- [x] `local_connector/file_access/`: the researcher registers a project folder and explicitly grants access (`python -m local_connector grant <folder>`)
- [x] Reading is allowed only inside granted folders. `.env`, credentials and private keys are always excluded (rules in `backend/orchestration/evidence_policy.py`)
- [x] `local_connector/evidence/`: extract only the relevant lines and files, never the whole repo
- [x] `POST /diagnose` in `backend/orchestration/`: symptom + selected evidence → CREAO adapter → diagnosis. Returns a job ID; `GET /diagnose/{job_id}` gives the result
- [x] Sample solver project with a real blow-up log in `tests/fixtures/sample_project/`

**Checkpoint 2**
- [x] Automated: `GET /health` returns 200
- [x] Automated: reading a file outside the granted folder is refused
- [x] Automated: `.env`, `*.pem`, `id_rsa` and similar files are never included, even inside a granted folder
- [x] Automated: `/diagnose` with mock CREAO returns a diagnosis, and the evidence stays under a size limit
- [x] Manual: one real `/diagnose` call on a sample error gives a sensible answer **(uses credits)** — passed 2026-10-23, run `e191c825`: with no hint in the symptom, the agent found `r = 0.30 > 0.25` from the config and code
- Note: the symlink-escape test is skipped on Windows machines that can't create symlinks

---

## Step 3 — SQLite models and demo data

**Tasks**
- [x] Tables in `backend/db/`: `workspaces`, `members`, `projects`, `project_members`, `agents`, `runs`, `private_episodes`, `shared_findings`, `finding_links`, `approvals` (`alerts` and `handoff_events` come in step 7). Shared tables go in `superbrain.db`; `runs` and `private_episodes` go in a separate file per researcher (`private/<member>.db`)
- [x] Demo data: 1 workspace, Researchers A and B, 2 projects. A belongs to both; B belongs to only one (needed for the isolation test). Also a group leader for step 7
- [x] Simple prototype sign-in: pick a demo researcher (`GET /demo/members`, `POST /sign-in`, `GET /me`)

**Checkpoint 3**
- [x] Delete the database file, recreate it and load the demo data: no errors (`python -m backend.workspaces.demo --reset`, run twice)
- [x] Automated: private episodes and shared findings are stored separately, and a private episode never appears in a shared query (checked down to the raw shared database file)
- [x] Automated: loading the demo data twice does not create duplicates
- [x] Automated: each researcher's private memory is separate and split by project; a researcher can't use a project they're not in; offboarding removes access at once

---

## Step 4 — Approval path to publish a finding

**Tasks**
- [x] Set up separate CREAO agents for Researchers A and B (`CREAO_AGENT_1_*` and `CREAO_AGENT_2_*` in `backend/.env`; the demo data already points A and B at these). If both used the one hub agent, CREAO's own long-term agent memory could carry A's evidence into B's runs, outside SimGuard's access checks. Both agents verified with a short run on 2026-10-23
- [x] Give both personal agents the same input contract (verified 2026-10-23: `AGENT_2` is now a copy of `AGENT_1`; see `backend/README.md`)
- [x] Configure the adapter for the personal agents' inputs (`personal_agent()`: `focus_areas` choices sent as comma-separated text, required `analysis_depth`, issue-list request). Verified on run `aaa9f564` **(uses credits)**
- [x] Use each signed-in researcher's own personal agent for `/diagnose` (requires sign-in and project membership; only the researcher can read their own diagnosis jobs)
- [x] An editable, minimum-necessary draft in one of the three categories. SimGuard builds it from the agent's top issue (symptom, likely cause, evidence, how to validate), always as a hypothesis, and keeps it in the researcher's private memory with the diagnosis
- [x] The researcher approves (optionally after editing) or rejects it (`POST /drafts/{id}/approve`, `POST /drafts/{id}/reject`)
- [x] On approval, store the shared finding with provenance (who, when, source project, CREAO run, verification status, whether it was edited) and its project label. Links between findings come with step 5, when a second finding exists to link to

**Checkpoint 4**
- [x] Automated: nothing reaches `shared_findings` without an approval record
- [x] Automated: a rejected draft stays private (not even its text is in the shared database file)
- [x] Automated: a hypothesis is still labelled a hypothesis after publishing, not verified
- [x] Manual: approve a draft, and it appears in the Super Brain **(uses credits)** — passed 2026-10-23, run `915f1428` on Researcher A's own agent: B saw nothing before approval, saw the finding after, and was refused on the wave project

---

## Step 5 — Scoped lookup for Researcher B

**Tasks**
- [x] Search shared findings filtered by B's workspace and project access, checked in code (never left to the AI). Access is decided in SQL before anything is ranked
- [x] Follow `finding_links` only to findings B is allowed to see (`GET /findings/{id}`)
- [x] Add retrieved findings as context to B's diagnosis request (`known_context`; on by default, `use_super_brain: false` turns it off). The query is only the researcher's symptom text. A diagnosis in project P only uses findings from projects that everyone in P can already read, so a shared result can't repeat restricted details
- [x] Manual "Ask Super Brain" search (`GET /superbrain/search`, `python -m local_connector ask`)
- [x] Link findings as approved knowledge arrives: approving a draft links its finding (`associated_with`) to the earlier findings its diagnosis used
- [x] Sample project for Researcher B (`tests/fixtures/sample_project_b/`): grid refined to `dx = 0.005` with `dt = 0.01`, a real blow-up log

**Checkpoint 5**
- [x] Automated: B retrieves A's approved finding from their shared project
- [x] Automated: B cannot retrieve a finding from the project they're not in, even by following a link
- [x] Automated: unapproved drafts are never returned
- [x] Manual: B's diagnosis mentions A's earlier finding **(uses credits)** — passed 2026-10-23, run `93ceb050` on Researcher B's own agent: "The prior shared hypothesis 744083a8 was relied on and independently corroborated, but … its `dt = 0.02` remedy would leave `r = 0.80` here — `dt_critical` scales with `dx²`". B's approved finding is linked to A's

---

## Step 6 — Dashboard and local anomaly checks

**Tasks**
- [ ] React dashboard in `frontend/` (views in [dashboard-design.md](dashboard-design.md)), polling the backend for updates
- [ ] `local_connector/simulation_monitor/`: local checks for NaN/Inf, large jumps, force spikes, missing outputs and configured thresholds, each labelled Informational, Warning or Critical
- [ ] Sample simulation output with planted problems in `tests/fixtures/`

**Checkpoint 6**
- [ ] Automated: each check flags its planted problem at the right severity and leaves clean output alone
- [ ] Manual: a Critical alert shows in the dashboard while the simulation keeps running
- [ ] Manual: the full A → approve → B flow works by clicking in the dashboard only **(uses credits)**
- [ ] Pages look right at 1280px and 1440px wide

---

## Step 7 — Optional: alert history and handoff (if time allows)

**Tasks**
- [ ] Alert history saving toggle, off by default
- [ ] Offboarding: revoke access → prepare eligible records → exclude private and sensitive data → transfer → verify → deactivate the agent

**Checkpoint 7**
- [ ] Automated: with saving off, nothing is written to `alerts`
- [ ] Automated: after offboarding, the member's requests are denied
- [ ] Automated: the handoff excludes credentials and private conversations
- [ ] Automated: a failed handoff is reported as failed, never as complete

---

## Final check — prototype definition of done

Demonstrate on one machine if necessary, with two demo researcher profiles in one workspace.

- [x] Create or select one demo workspace and two researcher profiles (step 3)
- [x] Register a local repository folder and read authorized relevant content (step 2)
- [x] Use the configured CREAO keys and known agent IDs to analyze a sample error (steps 1, 2 and 4)
- [x] Store private project-specific context and a group shared finding separately (steps 3 and 4)
- [ ] Show Informational / Warning / Critical alerts in the dashboard without stopping the simulation (step 6)
- [ ] Keep persistent alert saving opt-in and off by default (step 7)
- [x] Present a proposed knowledge contribution, require approval, and store only approved findings (step 4; dashboard view in step 6)
- [x] Researcher B can retrieve Researcher A's approved finding and use it to inform a diagnosis (step 5)
- [x] Demonstrate project isolation: an unauthorized researcher cannot retrieve restricted knowledge (steps 3–5)
- [ ] Demonstrate handoff → verification → deactivation for a demo agent (step 7)
- [ ] Never expose provider credentials or change source files without approval (holds so far; confirm at the end)
