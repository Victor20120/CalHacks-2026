# backend

FastAPI (Python) backend. It decides what data may be accessed, what is sent to CREAO and what shared findings each researcher may see. CREAO supplies reasoning, not authorization.

| Folder / file | Purpose |
|---|---|
| `api/` | FastAPI routes |
| `auth/` | Identity and project permission checks |
| `workspaces/` | Workspace and project management |
| `creao/` | CREAO API adapter |
| `orchestration/` | Agent workflows and evidence assembly |
| `memory/` | Hierarchical, episodic and linked-finding memory |
| `monitoring/` | Alerts and diagnoses |
| `knowledge_sharing/` | Approval and publishing of findings |
| `db/` | SQLite persistence |
| `testcreo.py` | Working CREAO test: starts a hub agent run and polls until it finishes. Uses credits |
| `requirements.txt` | Python packages: `python -m pip install -r backend/requirements.txt` |
| `.env.example` | Every environment variable name with no values. Copy it to `.env` and fill it in |

## Using the CREAO adapter

All CREAO calls go through `backend/creao/`. It checks required inputs before sending (a run with missing inputs still costs credits), waits for the result, and never logs the key.

```python
from backend.creao import CreaoClient, hub_agent

client = CreaoClient(hub_agent())  # reads CREAO_HUB_* from backend/.env
result = client.run({
    "project_path": "...",
    "observed_symptom": "...",   # pasted evidence, not a local path
    "focus_areas": "...",
})
result.reply          # prose reply (issue block removed)
result.summary        # counts, e.g. {"issues_confirmed": 2, ...}
result.issues         # [{"title", "status", "confidence", "evidence", "proposed_validation"}, ...]
result.analysis_ran   # False if the agent only answered (e.g. missing inputs)
```

An issue's `status` is the agent's own label (`confirmed` or `suspected`), not researcher verification. Anything not labelled exactly `confirmed` is treated as `suspected`.

For a researcher's personal agent, use `personal_agent("AGENT_1")` (or `"AGENT_2"`) instead of `hub_agent()`. It requires `analysis_depth` as well, and refuses values outside the fixed choices before sending, so a typo never costs credits:

```python
client = CreaoClient(personal_agent("AGENT_1"))
result = client.run({
    "project_path": "...",
    "observed_symptom": "...",
    "focus_areas": "numerical, reproducibility",  # one or more choices, comma-separated
    "analysis_depth": "standard",                  # triage, standard or deep
})
```

Errors are subclasses of `CreaoError`: `CreaoInputError` (nothing sent), `CreaoRequestError` (network, bad key, rate limit), `CreaoRunFailed`, `CreaoTimeout` (has the `run_id` to check later).

## Running the backend

```
python -m uvicorn backend.api.app:app --port 8000
```

It listens on this computer only (`127.0.0.1`), because sign-in is only a prototype: anyone who can reach the backend can sign in as any demo member.

| Endpoint | What it does |
|---|---|
| `GET /health` | Returns `{"status": "ok"}` |
| `GET /demo/members` | Demo members you can sign in as |
| `POST /sign-in` | Body: `member_id`. Returns a `token`; send it as `Authorization: Bearer <token>` |
| `GET /me` | The signed-in member and the projects they belong to |
| `POST /diagnose` | Signed in. Body: `project_id` (one of your projects), `symptom`, `focus_areas` (list; default `["numerical"]`), `analysis_depth` (default `standard`), `evidence` (list of `path`, `kind`, `excerpt`, `truncated`), `use_super_brain` (default `true`). Checks the evidence, looks up earlier findings, starts a run on **your own** personal agent and returns `202` with `job_id`, `evidence_sent`, `redactions` and `prior_findings` |
| `GET /diagnose/{job_id}` | Signed in, your own jobs only. `status` is `running`, `completed` (with `diagnosis`: `reply`, `issues`, `summary`, `run_id`; `prior_findings`; and `draft_id`) or `failed` (with `error`: `type`, `message`, sometimes `run_id`). A completed diagnosis is saved to your private memory |
| `GET /drafts` | Your pending drafts for the Super Brain. Only you can see them |
| `POST /drafts/{draft_id}/approve` | Publish a draft as a shared finding. Optional edits: `title`, `summary`, `category`, `verification_status` |
| `POST /drafts/{draft_id}/reject` | Keep a draft private for good. Nothing is written to the shared database |
| `GET /projects/{project_id}/findings` | Approved findings in a project you belong to |
| `GET /superbrain/findings` | Approved findings across every project you belong to, newest first, with who shared each one. Optional `project_id` narrows it |
| `GET /superbrain/search?q=...` | "Ask Super Brain": approved findings in your projects, ranked by shared words. Optional `project_id` narrows it |
| `GET /findings/{finding_id}` | One finding and its links, leaving out links to findings you can't read. Findings in projects you can't read return `404` |
| `POST /alerts` | From your local watcher. Body: `project_id` (one of your projects), `run_label`, `alerts` (list of `severity`, `check`, `message`, `metric`, `line_number`, `line`). Kept in memory for this session only |
| `GET /alerts` | Your own alerts, newest first (optional `project_id`). Other researchers never see them |
| `DELETE /alerts` | Clear your alerts |

| `GET /settings`, `PUT /settings` | Your settings: `save_alert_history` (off by default) |
| `GET /alerts/history`, `DELETE /alerts/history` | Your saved alerts (only while history is on), or delete them |
| `GET /policy` | Membership policies disclosed in advance (offboarding) |
| `GET /members` | Everyone in the group, including offboarded members. Leader only |
| `POST /offboarding` | Body: `member_id`. Hand a leaving member's eligible work to the Super Brain, verify it, retire their agent. Leader only. Returns a step-by-step report |
| `GET /offboarding` | Past handoffs. Leader only |
| `POST /overview` | Body: `project_id`, `tree`, `files`, `excerpts`, `stats` (a project map from the local connector). Re-checks it, then starts an overview on your own agent. Returns `202` with `job_id` |
| `GET /projects/{project_id}/overview` | Your saved overview of that project (purpose, structure, how to run, key parameters, open questions). Only you can see it |
| `POST /projects` | Body: `id`, `name`, `description`, `member_ids`. Create a project in your group (leader only; the leader is always a member) |
| `GET /jobs/{job_id}` | Any of your jobs (diagnosis or overview); same as `GET /diagnose/{job_id}` |
| `POST /simulation-runs` | Body: `project_id`, `run_label`. Put a run you started watching on the dashboard (sent by your local connector). Returns `201` |
| `POST /simulation-runs/{run_id}/report` | Body: `points` (list of `step` and `values`, a metric name to a number, or `null` for NaN/Inf), `alerts`, `stopped`. Only the researcher watching the run can report |
| `GET /simulation-runs` | Runs you may see, newest first (optional `project_id`): your own, and for the group leader every run in their projects. Each has who, when, the last step, alert counts and its metric names |
| `GET /simulation-runs/{run_id}` | One run with its numbers per metric (for graphs) and its alerts. Runs you can't see return `404` |
| `GET /simulation-runs/alerts` | The newest alerts from runs you may see (optional `limit`, up to 100) |

Session alerts are never written to a database, so they disappear when the backend restarts. With alert history turned on, alerts are also saved in your private memory file, never in the shared database.

A watched run is kept in the shared database (`simulation_runs`, `run_points`, `run_alerts`): the numbers the connector read from each step and the run's alerts, never the log file. Only the researcher and the group leader (if they are a member of the run's project) can see it; other researchers never do. A run keeps up to 6 metrics; past 1,000 points per metric every other point is dropped, keeping the last one and every NaN/Inf point.

### Central CREAO hub conversation

| Endpoint | Purpose |
|---|---|
| `POST /superbrain/chat` | Body: `project_id`, `message` (up to 4,000 characters), `scope` (`project` or `all`). Starts a CREAO response on the dedicated chat agent, or on the hub until it is set up, and returns `202` with `job_id`, selected `prior_findings`, `knowledge_stats`, `agent_slot` (`CHAT` or `HUB`), and any updated `instructions`. Poll `GET /jobs/{job_id}` for `diagnosis.reply` |
| `POST /agent/chat` | Compatibility alias for the same central-hub conversation |
| `GET /projects/{project_id}/chat?scope=project` | Your last 12 completed exchanges for this knowledge scope and a matching `pending_job_id` |
| `DELETE /projects/{project_id}/chat?scope=project` | Clear this local conversation; refuses while a reply runs. Keeps rules and does not reset provider memory |
| `GET /projects/{project_id}/agent-rules` | Your saved `instructions` and `default_instructions` |
| `PUT /projects/{project_id}/agent-rules` | Body: `instructions` (up to 2,000 characters). Blank restores defaults. Applied to subsequent chat, diagnosis, and overview requests |

**Knowledge flow:** personal CREAO agent A/B diagnosis → private draft → researcher approval → shared `shared_findings` record → retrieval for the current question → `CREAO_CHAT` (or `CREAO_HUB`) request → synthesized answer. There is no second provider ingestion call at approval time: the shared database is the durable knowledge store, and the hub receives the retrieved specialized records when answering. New approved contributions are available on the next question.

All chat replies use `hub_agent()`, regardless of who asks. Personal diagnoses and overviews still use the researcher's personal agent. The hub receives the user's current message, saved response rules, bounded recent conversation, and structured specialized knowledge. Each selected record retains its full published summary (up to 4,000 characters), author, project, verification label, and source agent run. At most eight records enter a 40,000-character context, and recent conversation stays under 4,500 characters. The prompt permits general scientific knowledge and reasoning, while requiring project-specific claims to come from retrieved records. No private project overview is sent to the shared hub.

Greetings, empty retrieval, and preference changes also go to CREAO; there is no local-answer or lookup branch. Natural standalone style directives are saved before starting the request. `/rules <text>` replaces preferences, `/rules show` asks the hub to explain them, and `/rules reset` restores defaults. The hub is told which preference action the application completed, so acknowledgments reflect saved state.

Chat polls CREAO every second; the browser polls the local job every 750 ms. `elapsed_ms`, `provider_ms`, and `prompt_chars` expose latency. Selected sources are shown while the provider works. This reduces delivery overhead without replacing CREAO's reasoning.

Retrieval and local history/job reads check current project membership. All-project scope uses only the viewer's projects; older combined answers are hidden if relevant access is removed. These checks govern application-supplied records. The configured shared CREAO hub may retain provider-side memory across runs; strict isolation of that memory requires provider configuration or separate hubs and is not guaranteed by prompt instructions. Rules and conversation history are persisted in the user's private settings table; no schema reset is needed. Chat never creates a sharing draft. One hub chat run per user/project at a time; duplicates return `409`.

### Chat agent, model and latency

Chat speed is set by CREAO's run, not by the app: the app prepares a question in about 50 ms and checks for the answer every second. Measured on real runs, the hub spent 21 s on "hello" and 35 to 46 s on questions, because it runs its full audit workflow (skills, tools) on every message. A faster model alone did not help (54 to 60 s), so chat now:

- runs on the dedicated chat agent when `CREAO_CHAT_APP_ID` and `CREAO_CHAT_KEY` are set: a CREAO agent with only `question` and `context` inputs and no tools ([setup](../Documents/super-brain-chat-agent.md)). It uses an ordinary `capi_` app key. Until then, the hub;
- asks the hub for a direct answer from the supplied context, without an audit, tools or skills;
- sends `chatModelId` from `CREAO_CHAT_MODEL_ID` (empty: `gemini-3-flash`; `saved`: the agent's own model).

Diagnoses and project overviews still use each researcher's own agent with its saved model and workflow. The [speed benchmark](../Documents/benchmarks/hub-chat-speed-2026-10-24.md) records the earlier comparison of these settings.

### Offboarding

The one exception to per-finding approval, published in advance as a membership policy (`backend/knowledge_sharing/offboarding.py`):

1. The group leader starts it, and the member's access is revoked at once.
2. Eligible work is prepared from their private memory: diagnoses (outcome and issue list, not the agent's conversation), test results, debugging outcomes, unresolved tasks and drafts they hadn't decided on.
3. Left out: personal notes, drafts they rejected, anything already shared and anything outside this group's projects. Secrets are blanked, and each record keeps its project, so only that project's members can read it. A handoff never marks anything "verified".
4. The records are transferred and verified in one transaction. If that fails, nothing is stored, the handoff is recorded as failed, and the agent stays active so the leader can retry.
5. The member's personal agent is retired. Their private memory is not deleted (separate retention policy).

### How the Super Brain is used in a diagnosis

- The search query is only the researcher's own symptom text, never the evidence.
- Results are limited to projects the researcher belongs to, decided in SQL before ranking.
- A diagnosis in project P only uses findings from projects that everyone in P can already read (`context_project_ids` in `backend/auth/permissions.py`). Otherwise a summary written from them, once shared in P, could repeat restricted details. In the demo, a heat diagnosis never uses wave findings, but a wave diagnosis may use heat findings.
- Up to 3 findings (4,000 characters) go to the agent's `known_context` input, each labelled with its verification status, with an instruction to treat them as evidence to check, not facts.
- When a draft from such a diagnosis is approved, the new finding is linked `associated_with` the findings it used (not `similar_to`: using a finding as context doesn't show the problems are the same).

## Databases

SQLite files in `backend/data/` (git ignores it; set `EXPANDRG_DATA_DIR` to move it). They are created with the demo workspace on first use.

| File | Holds |
|---|---|
| `superbrain.db` | The group's shared Super Brain: workspace, members, projects, agents, approvals, shared findings, their links, and offboarding handoffs |
| `private/<member>.db` | One researcher's private memory (runs, episodes, drafts for the Super Brain, settings, and saved alerts if they turned history on), split by project. In the product this lives on the researcher's own computer |

Private and shared data are in different files with different tables, so no shared query can return private memory. `approvals` stores no draft text: rejected drafts never reach the shared database. Approving writes the approval and the finding in one transaction.

When the tables change, `SCHEMA_VERSION` in `backend/db/schema.py` goes up and older database files are refused with instructions to reset them (there are no migrations in the prototype).

Demo workspace: a group leader, Researcher A (projects `heat-diffusion` and `wave-propagation`), Researcher B (`heat-diffusion` only) and Researcher C (`heat-diffusion`, for the offboarding demo, with sample private work history).

```
python -m backend.workspaces.demo            # add what's missing (e.g. Researcher C's history), keep everything else
python -m backend.workspaces.demo --reset    # delete everything and start again
```

## Evidence rules

`backend/orchestration/evidence_policy.py` is shared by the local connector and the backend, and the backend re-checks everything it receives:

- At most 10 excerpts, 4,000 characters each, 12,000 in total.
- Paths must be relative to the project folder, so local paths (which can contain the user name) never leave the computer.
- Sensitive files are never sent: `.env`, `.env.*`, private keys (`id_rsa`, `*.pem`, `*.key`, …), credential files, and anything inside `.git`, `.ssh`, `.aws` and similar folders.
- Secrets inside files (private key blocks, CREAO, AWS and GitHub keys, `password = …`-style values) are blanked out before sending.

## CREAO API: verified so far

Base URL `https://agent.creao.ai/api/v1`, header `Authorization: Bearer <capi_ key>`.

| Request | Verified by | Notes |
|---|---|---|
| `POST /runs` with `{"appId": ..., "inputs": {...}}` | `testcreo.py` | Starts a run and returns `id` |
| `GET /runs/{id}` | `testcreo.py` | Poll until `status` is `completed`, `failed` or `cancelled` |
| `GET /apps/{appId}/runs` | Manual check, 2026-10-23 | Lists past runs. Uses no credits. Returns 200 even for an app ID that doesn't exist, so it can't confirm an app ID |

- **Agent inputs** (checked 2026-10-23 with a short probe run on each agent):

  | Slot | Used by | Required inputs | Optional inputs |
  |---|---|---|---|
  | `HUB` | Tested pipeline so far | `project_path`, `observed_symptom`, `focus_areas` (free text) | `analysis_depth` (defaults to `standard`) |
  | `AGENT_1`, `AGENT_2` | Researchers A and B (same contract, runs `8eb4dc7d` and `7bcbb28f`) | `project_path`, `observed_symptom`, `focus_areas` (one or more of `numerical`, `reproducibility`, `data_integrity`, `performance`, `visualization`, `all`), `analysis_depth` (`triage`, `standard`, `deep`) | `artifacts` (file uploads), `known_context` (prior findings already ruled out or fixed), `expected_behavior` (reference value or target) |

  Several `focus_areas` are sent as comma-separated text, e.g. `"numerical, reproducibility"` (verified on run `aaa9f564`: the agent echoed both back and analyzed at `standard` depth). The issue-list request works on these agents too (that run returned 10 issues matching its summary counts). A run without the required inputs still completes (and costs credits) but only reports what's missing.
- **Keys aren't limited to one agent in practice:** each agent has its own `capi_` key, but any of our keys can list and read the other agents' runs. CREAO's docs say a key is scoped to one agent. So ExpandRG must choose each member's app ID in its own code. CREAO keeps agent memory per app ID, not per key.
- **Agent output:** `result.chatText` is the prose reply. `result.text` is a JSON string whose shape depends on the outcome: `{"response": ...}` when inputs were missing, or the inputs echoed back plus summary counts (`issues_confirmed`, `issues_suspected`, `blocking_count`, `reproducibility_score`, `experiments_proposed`) when the analysis ran. Agents can also return counts when nothing was analyzed (all zeros, with a `reproducibility_score` of `0` or `"not_computed"`). The reliable signal is the echoed `analysis_depth`: a real depth (`triage`, `standard`, `deep`) on real analyses, `none` or `not_run` otherwise. The adapter sets `analysis_ran` from that.
- **Issue list:** the agent reads inputs it doesn't declare. `hub_agent()` sends an extra `output_format` input with every run, asking the agent to end its reply with a JSON block listing each issue. The adapter moves that block into `result.issues` (verified on run `790fe5fd`: 6 issues matching the summary counts).
- A full diagnosis took about 60 seconds. The response includes no credit usage and no artifacts.
- Known-good example run: `tests/fixtures/creao_hub_run_completed.json` (app ID redacted).
- CREAO runs in the cloud and cannot read local folders. Send evidence text, not a local path.
- Replies can contain symbols like `α`, which crash `print()` in a Windows terminal. Set `PYTHONIOENCODING=utf-8` before running scripts that print replies.
