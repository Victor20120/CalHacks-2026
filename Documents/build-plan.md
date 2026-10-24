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
| 6 | Dashboard and local anomaly checks | `frontend/`, `local_connector/simulation_monitor/`, `backend/monitoring/` | Done |
| 7 | Optional: alert history and handoff | `backend/memory/`, `backend/knowledge_sharing/` | Done |
| 8 | Project overview | `local_connector/evidence/`, `backend/orchestration/overview.py` | Built; real-project test pending |

---

## Standing checks (before every push)

- [ ] All tests pass (`python -m pytest` from the repo root), using mock CREAO only, and the dashboard builds (`npm run build` in `frontend/`)
- [ ] `git status` shows no `.env`, `.db` files or other secrets about to be committed
- [ ] No code edits source files or stops a simulation without the researcher's approval
- [ ] Any dashboard page you changed looks right at 1280px and 1440px wide

---

## Step 0 — Setup

**Tasks**
- [x] CREAO API Trigger key (`capi_`) and app ID for the hub agent in `backend/.env`
- [x] `backend/.env.example` listing every variable name (no values)
- [x] Python and Node.js installed, repo cloned, `backend/.env` created (solo project, so there are no teammates to set up)
- [x] `backend/requirements.txt` (requests, python-dotenv, pytest; FastAPI and uvicorn get added in step 2)
- [x] Python virtual environment: `calhack`, in the folder above the repo (VS Code turns it on in its terminal). The project's packages are installed in it

**Checkpoint 0**
- [x] `git check-ignore backend/.env` prints `backend/.env`
- [x] `backend/testcreo.py` starts a run and gets the result back (2026-10-23) **(uses credits)**
- [x] `testcreo.py` runs with the developer's own `.env` (solo project)

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
- [ ] Record the credits a run uses. Someone has to read this in the CREAO app, because the `capi_` key can't see usage. Full-analysis runs to look up: `dd8668fa`, `790fe5fd`, `e191c825`, `915f1428`, `93ceb050`, `aaa9f564`, `461fed21`, `66a5d826`, `66bc53ce`; short probe runs: `bc7a5d26`, `829188d5`, `8eb4dc7d`, `7bcbb28f`
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
- [x] Set up separate CREAO agents for Researchers A and B (`CREAO_AGENT_1_*` and `CREAO_AGENT_2_*` in `backend/.env`; the demo data already points A and B at these). If both used the one hub agent, CREAO's own long-term agent memory could carry A's evidence into B's runs, outside ExpandRG's access checks. Both agents verified with a short run on 2026-10-23
- [x] Give both personal agents the same input contract (verified 2026-10-23: `AGENT_2` is now a copy of `AGENT_1`; see `backend/README.md`)
- [x] Configure the adapter for the personal agents' inputs (`personal_agent()`: `focus_areas` choices sent as comma-separated text, required `analysis_depth`, issue-list request). Verified on run `aaa9f564` **(uses credits)**
- [x] Use each signed-in researcher's own personal agent for `/diagnose` (requires sign-in and project membership; only the researcher can read their own diagnosis jobs)
- [x] An editable, minimum-necessary draft in one of the three categories. ExpandRG builds it from the agent's top issue (symptom, likely cause, evidence, how to validate), always as a hypothesis, and keeps it in the researcher's private memory with the diagnosis
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
- [x] React dashboard in `frontend/` (views in [dashboard-design.md](dashboard-design.md)), polling the backend for updates
- [x] `local_connector/simulation_monitor/`: local checks for NaN/Inf, large jumps, force spikes, missing outputs and configured thresholds, each labelled Informational, Warning or Critical. Read-only
- [x] Sample simulation output with planted problems in `tests/fixtures/` (planted lines in `tests/test_monitoring.py`, the two real blow-up logs, and a clean run in `tests/fixtures/monitor/stable.log`)
- [x] Alerts in the backend: `POST /alerts` from the researcher's watcher, `GET /alerts` for the dashboard. Session-only, private to each researcher
- [x] The local connector as a small service on `127.0.0.1:8001` (`python -m local_connector serve`), since a browser can't read local files: granted folders, file lists, evidence previews and log watching

**Checkpoint 6**
- [x] Automated: each check flags its planted problem at the right severity and leaves clean output alone
- [x] Manual: a Critical alert shows in the dashboard while the simulation keeps running — passed 2026-10-24 with a real solver run watched through the connector; screenshot taken while it ran, and the solver ended on its own (exit code 1 from its own error). Also an automated test (`test_the_simulation_keeps_running_after_a_critical_alert`)
- [x] Manual: the full A → approve → B flow works by clicking in the dashboard only **(uses credits)** — passed 2026-10-24, automated with Playwright driving Edge (runs `66a5d826` and `66bc53ce`): A diagnosed and shared by clicking; B's diagnosis showed A's finding under "Retrieved from the Super Brain"; B shared, and B's finding is linked to A's. This time B's agent answered the dx² trap correctly but didn't name A's finding in its reply (it did in step 5's run `93ceb050`). The first attempt (run `461fed21`) found a real bug: a full analysis returned without a summary was taken for "no analysis", so no draft was made. Fixed in the adapter, with that run saved as a test fixture
- [x] Pages look right at 1280px and 1440px wide — checked with headless-browser screenshots of every view at both widths; below 1280px a "built for laptop and desktop" notice appears

---

## Step 7 — Optional: alert history and handoff (if time allows)

**Tasks**
- [x] Alert history saving toggle, off by default (`GET`/`PUT /settings`). While on, alerts are also saved to the researcher's private memory (`GET`/`DELETE /alerts/history`); never to the shared database
- [x] Offboarding (`POST /offboarding`, leader only): revoke access → prepare eligible records → exclude private and sensitive data → transfer → verify → deactivate the agent. Handed-off findings keep their project and are marked "handed off at offboarding"
- [x] The membership policy is published in advance (`GET /policy`) and shown on the sign-in page and in the leader's Offboarding view
- [x] Demo: Researcher C with sample private history (`python -m backend.workspaces.demo`), so offboarding can be shown without touching A and B

**Checkpoint 7**
- [x] Automated: with saving off, nothing is written to `alerts` (checked down to the raw database files)
- [x] Automated: after offboarding, the member's requests are denied
- [x] Automated: the handoff excludes credentials and private conversations (also personal notes, drafts the member rejected, and other groups' content)
- [x] Automated: a failed handoff is reported as failed, never as complete (nothing is stored, the agent stays active, and the leader can retry)
- [x] Manual: the leader offboarded Researcher C by clicking in the dashboard — 3 records handed off, 2 left out, every step ✓; C disappeared from sign-in and B saw C's work marked "handed off at offboarding"

---

## Step 8 — Project overview ("What is this project?")

**Tasks**
- [x] `local_connector/evidence/project_map.py`: a map of the whole granted folder, built on the researcher's computer: the folder tree (big folders summarized by file type) and the start of key files (README, dependency and build files, configs, an outline of the main scripts, notebook headings). Tests, tool folders, sensitive and binary files are left out; secrets are blanked; the map stays under 24,000 characters
- [x] The researcher previews the exact map before sending (dashboard **Project** view, or `python -m local_connector overview`)
- [x] `backend/orchestration/overview.py` and `POST /overview`: the backend re-checks the map, then asks the researcher's own agent for an overview (purpose, structure, how to run, key parameters, open questions). Paths the agent invents are marked "not found in the project"
- [x] The overview is saved privately (`GET /projects/{id}/overview`) and included as project context in every later diagnosis on that project
- [x] Real projects can be added to the workspace: `POST /projects` (leader) or `python -m backend.workspaces.demo --add-project ...`

**Checkpoint 8**
- [x] Automated: the map covers the project but never includes sensitive, binary, tool or test files; it stays within its size limit (`tests/test_project_overview.py`)
- [x] Automated: unsafe or oversized maps are refused by the backend; the overview is private; later diagnoses include it
- [ ] Manual: build an overview of a real research project **(uses credits)**, and check whether the personal agent (an audit agent) explains the project or only audits it

---

## Presenting

- [x] One-command demo launcher: `python run_demo.py` (or `--fresh`) starts the backend, local connector and dashboard, grants the sample folders, and opens Researcher A and B side by side. Checked with `--check`: everything starts, and everything stops afterwards
- [x] Demo script for one presenter playing every role: [demo-script.md](demo-script.md)
- [ ] Rehearse once and make a backup screen recording **(uses credits)**

---

## Final check — prototype definition of done

Demonstrate on one machine if necessary, with two demo researcher profiles in one workspace.

- [x] Create or select one demo workspace and two researcher profiles (step 3)
- [x] Register a local repository folder and read authorized relevant content (step 2)
- [x] Use the configured CREAO keys and known agent IDs to analyze a sample error (steps 1, 2 and 4)
- [x] Store private project-specific context and a group shared finding separately (steps 3 and 4)
- [x] Show Informational / Warning / Critical alerts in the dashboard without stopping the simulation (step 6)
- [x] Keep persistent alert saving opt-in and off by default (step 7)
- [x] Present a proposed knowledge contribution, require approval, and store only approved findings (step 4; dashboard view in step 6)
- [x] Researcher B can retrieve Researcher A's approved finding and use it to inform a diagnosis (step 5)
- [x] Demonstrate project isolation: an unauthorized researcher cannot retrieve restricted knowledge (steps 3–5)
- [x] Demonstrate handoff → verification → deactivation for a demo agent (step 7)
- [x] Never expose provider credentials or change source files without approval (`tests/test_definition_of_done.py`: no backend response contains a key or app ID, even when CREAO rejects a key; the dashboard source never handles keys; a full connector session leaves every project file byte-for-byte unchanged; the connector writes nothing but its own grants file)
