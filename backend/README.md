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
| `GET /me` | The signed-in member, their projects, and public personal-agent identity (`id`, `number`, `name`), or `null` for no active agent |
| `POST /diagnose` | Signed in. Body: `project_id` (one of your projects), `symptom`, `focus_areas` (list; default `["numerical"]`), `analysis_depth` (default `standard`), `evidence` (list of `path`, `kind`, `excerpt`, `truncated`), `use_super_brain` (default `true`). Checks the evidence, looks up earlier findings, starts a run on **your own** personal agent and returns `202` with `job_id`, `evidence_sent`, `redactions` and `prior_findings` |
| `GET /diagnose/{job_id}` | Signed in, your own jobs only. `status` is `running`, `completed` (with `diagnosis`: `reply`, `issues`, `summary`, `run_id`; `prior_findings`; and `draft_id`) or `failed` (with `error`: `type`, `message`, sometimes `run_id`). A completed diagnosis is saved to your private memory |
| `GET /drafts` | Your pending drafts for the Super Brain. Only you can see them |
| `POST /drafts/{draft_id}/approve` | Publish a draft as a shared finding. Optional edits: `title`, `summary`, `category`, `verification_status`, and `run_id`: one of your own runs in the draft's project, attached so its graph shows with the update to whoever may see that run (`422` for anyone else's run). A personal agent's chat share carries the run the agent looked at, when it's yours and in that project |
| `POST /drafts/{draft_id}/reject` | Keep a draft private for good. Nothing is written to the shared database |
| `GET /projects/{project_id}/findings` | Approved findings in a project you belong to |
| `GET /superbrain/findings` | Approved findings across every project you belong to, newest first, with who shared each one. Optional `project_id` narrows it |
| `GET /superbrain/search?q=...` | "Ask Super Brain": approved findings in your projects, ranked by shared words. Optional `project_id` narrows it |
| `GET /findings/{finding_id}` | One finding, its links (leaving out links to findings you can't read), its `notes` and its `comments`. Every finding carries `run_id` (the run it came from, or `null`) and its number of `comments`. Findings in projects you can't read return `404` |
| `GET /findings/{finding_id}/comments` | The comments on a finding you can read, oldest first, with who wrote each one and their role |
| `POST /findings/{finding_id}/comments` | Body: `text` (up to 2,000 characters). Feedback that the leader and the project's researchers all see (`201`). Secrets are blanked. Agents don't read comments; notes are for that |
| `POST /findings/{finding_id}/notes` | Body: `kind` (`detail`, `guess` or `correction`), `text` (up to 2,000 characters). Adds your note to a finding you can read and returns it (`201`). Secrets are blanked. Findings you can't read return `404` |
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
| `POST /projects` | Body: `name`, optional `id` (default: from the name, lowercase with dashes), `description`, `member_ids`. Create a project in your group (leader only; the leader is always a member). In a group made with `setup_expandrg.py` (`EXPANDRG_GROUP` set), it's also added to the group's `group.json`, so VS Code's **Set up** lists it |
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

**Knowledge flow:** personal CREAO agent A/B diagnosis → private draft → researcher approval → shared `shared_findings` record (or: personal agent chat → the agent shares it, see [Sharing from chat](#sharing-from-chat)) → retrieval for the current question → `CREAO_CHAT` (or `CREAO_HUB`) request → synthesized answer. There is no second provider ingestion call at approval time: the shared database is the durable knowledge store, and the hub receives the retrieved specialized records when answering. New approved contributions are available on the next question.

All chat replies use `hub_agent()`, regardless of who asks. Personal diagnoses and overviews still use the researcher's personal agent. The hub receives the user's current message, saved response rules, bounded recent conversation, and structured specialized knowledge. Each selected record retains its full published summary (up to 4,000 characters), author, project, verification label, and source agent run, plus `shared_by` for chat shares, the agent's own guess, and members' notes (detail, guesses and corrections). At most eight records enter a 40,000-character context, and recent conversation stays under 4,500 characters. The prompt permits general scientific knowledge and reasoning, while requiring project-specific claims to come from retrieved records. No private project overview is sent to the shared hub.

Greetings, empty retrieval, and preference changes also go to CREAO; there is no local-answer or lookup branch. Natural standalone style directives are saved before starting the request. `/rules <text>` replaces preferences, `/rules show` asks the hub to explain them, and `/rules reset` restores defaults. The hub is told which preference action the application completed, so acknowledgments reflect saved state.

Chat polls CREAO every second; the browser polls the local job every 750 ms. `elapsed_ms`, `provider_ms`, and `prompt_chars` expose latency; the dashboard keeps them, and the findings used, out of sight, so only the answer shows. This reduces delivery overhead without replacing CREAO's reasoning.

Retrieval and local history/job reads check current project membership. All-project scope uses only the viewer's projects; older combined answers are hidden if relevant access is removed. These checks govern application-supplied records. The configured shared CREAO hub may retain provider-side memory across runs; strict isolation of that memory requires provider configuration or separate hubs and is not guaranteed by prompt instructions. Rules and conversation history are persisted in the user's private settings table; no schema reset is needed. Super Brain chat never creates a draft or a finding. One hub chat run per user/project at a time; duplicates return `409`.

### Chat agent, model and latency

Chat speed is set by CREAO's run, not by the app: the app prepares a question in about 50 ms and checks for the answer every second. Measured on real runs, the hub spent 21 s on "hello" and 35 to 46 s on questions, because it runs its full audit workflow (skills, tools) on every message. A faster model alone did not help (54 to 60 s), so chat now:

- runs on the dedicated chat agent when `CREAO_CHAT_APP_ID` and `CREAO_CHAT_KEY` are set: a CREAO agent with only `question` and `context` inputs and no tools ([setup](../Documents/super-brain-chat-agent.md)). It uses an ordinary `capi_` app key. Until then, the hub;
- asks the hub for a direct answer from the supplied context, without an audit, tools or skills;
- sends `chatModelId` from `CREAO_CHAT_MODEL_ID` (empty: `gemini-3-flash`; `saved`: the agent's own model).

Diagnoses and project overviews still use each researcher's own agent with its saved model and workflow. The [speed benchmark](../Documents/benchmarks/hub-chat-speed-2026-10-24.md) records the earlier comparison of these settings.

### Personal CREAO agent chat

| Endpoint | Purpose |
|---|---|
| `POST /personal-agent/chat` | Body: `project_id`, `message` (up to 4,000 characters), optional `folder` (a granted folder's name, never a path: lets the agent read its files). Starts a reply on the signed-in researcher's own active CREAO agent. Returns `202` with `job_id`, any updated `instructions`, `agent` identity, and `used_project_overview`. Poll `/jobs/{job_id}`; `diagnosis.shared_finding` is the finding the agent shared from this reply, or `null` |
| `GET /projects/{project_id}/personal-chat` | That user's personal conversation, public `agent` identity, and any `pending_job_id` |
| `DELETE /projects/{project_id}/personal-chat` | Clear personal history while keeping instructions and overview. Refuses while a personal reply is running |
| `POST /agent-tools` | Body: `project_id`, optional `folder`, `tool`, `args`. Runs one of the agent's read-only tools as you, with your permissions, and returns `ok`, `summary` and `result` or `error` |

**Toolkit.** The personal agent can list and read research files in the granted folder, search the Super Brain and look at the researcher's runs ([toolkit](../Documents/agent-toolkit.md), `backend/orchestration/toolkit.py`). CREAO can't call ExpandRG's tools, so the agent ends a reply with a `{"tool_requests": [...]}` block. ExpandRG runs up to 5 requests per round, 2 rounds per message (at most 3 CREAO runs), and calls the agent again with the results. File reads go through the local connector (`EXPANDRG_CONNECTOR`, default `http://127.0.0.1:8001`). The reply ends with plain footnotes: `Sources:` (the files it read by name, runs, Super Brain findings) and, if any, `Couldn't open:`. The details of each request, including why one failed, stay in `tool_calls`. The agent is told to write as a professional colleague: no mention of skills, manifests, tools or its process, formulas in plain text rather than LaTeX, and nothing that refers to lists outside the reply. Job results carry `tool_calls` (summaries only) and `creao_runs`. The agent is also told never to run project code in CREAO's sandbox.

This uses the existing personal agent contract (`AGENT_1` for A, `AGENT_2` for B), with configured CREAO skills, no hub/dedicated-chat routing, and no chat model override. The required personal-agent `analysis_depth` input is `standard`. Context consists of the user's saved project overview, recent personal conversation, their response instructions, and approved shared findings in this project. User text and replies are redacted; history uses a separate project-scoped private settings key, so it cannot appear in Super Brain chat or offboarding findings. Only what the agent shares (below) leaves it.

Personal chat defaults to direct, conversational answers: short paragraphs for simple questions, more structure when requested, and no visible skill-loading narration. Configured CREAO skills remain available. A narrow presentation filter removes a leaked opening sentence about reading internal skill files before saving a reply; it preserves explanations about skills and statements about unavailable access. Older setup narration is also removed from the bounded conversation context sent on follow-up requests. Say "Be conversational and answer directly" to save that preference through the normal rules workflow.

Natural style directives and `/rules` commands reuse the existing per-user/project agent instructions. Chat only returns proposals and executes no commands. Leaders have no personal agent and are refused. Job/history endpoints re-check membership, and one personal chat job per user/project runs at a time independently of hub chat. The VS Code panel uses the existing `/project-map` → preview → `/overview` flow for project understanding.

Personal agents are numbered within their workspace by registration/activation order (`agents.created_at`, with insertion order breaking timestamp ties). Retired agents remain in the ordering, so the remaining agents are not renumbered. The CREAO credential prefix does not determine the public number. Identity is returned by `/me` and personal-chat endpoints, included in each personal CREAO prompt, and saved with replies. New conversations request an introduction using the assigned name. No extra provider request or database reset is needed to show an identity. Restart a backend already running older code to load this change.

### Worksheets: the Super Brain as a teacher

| Endpoint | Purpose |
|---|---|
| `POST /worksheets` | Leader only. Body: `project_id`, optional `member_ids` (who gets one; default everyone with something to learn), `only_new` (default `true`), `request` (what to cover, up to 2,000 characters), `title` (your own title for them), `questions` (up to 8 of `{kind: "question" or "try", prompt, model_answer}`, written by you for the Super Brain to finish). Starts one Super Brain run (`202`, poll `/jobs/{job_id}`; the result has `worksheet_ids`). Returns `researchers` (who gets one, and how many findings they learn from) and `findings`. With a `request` or `questions`, everyone picked gets one, even with nothing new to learn from. `422` when no one picked has anything (new) to learn and nothing was asked, or when nobody is picked; `409` while one is running |
| `GET /worksheets/next?project_id=&only_new=` | Leader only. For each researcher, `new_findings` (findings from the others they'd learn from; with `only_new`, new since their last worksheet that wasn't cancelled) and `since`. Starts nothing |
| `POST /worksheets/{id}/cancel`, `/restore` | Leader only. Cancel or restore one worksheet |
| `GET /worksheets?project_id=` | Every worksheet in the project for the leader; your own for a researcher. Each has `number`, `status` (`assigned`, `submitted`, `reviewed`), `items`, `answered` and `cancelled` |
| `GET /worksheets/{id}` | One worksheet with its items. A researcher sees `model_answer` only once the leader has reviewed it |
| `PUT /worksheets/{id}/answers` | The researcher it's for. Body: `answers` (`{item_id: text}`), `submit`. `409` once reviewed |
| `POST /worksheets/{id}/review` | Leader only. Body: `comments` (`{item_id: text}`), `comment`. Marks it reviewed |
| `DELETE /worksheets/{id}` | Leader only |

`GET /worksheets` lists the newest first. `number` is the worksheet's number for its researcher in that project (Worksheet 1, 2, ...): one more than the worksheets made for them before it that weren't cancelled. So a cancelled worksheet keeps showing its number to the leader, the next one made for that person takes the same number, and restoring the cancelled one moves the later ones up by one. Researchers never see cancelled ones (`404` when opened). Each Super Brain run's worksheets are stored as a batch in `worksheet_weeks` and `worksheet_in_week` (names from when batches were weeks; `worksheet_in_week.cancelled_at` marks a cancelled worksheet). A batch cancelled as a whole in older data becomes each of its worksheets cancelled when the project's worksheets are next read, so each can be restored on its own. Worksheets made before batches existed get one batch per Super Brain run.

`backend/knowledge_sharing/teacher.py` lists, for each active researcher, the findings the *others* shared that are new to them: shared, or given a note, after their last worksheet that wasn't cancelled. That's everything in a first worksheet or with `only_new=false`, up to 30. The leader's `request` and `questions` go along as `leader_request` and `leader_questions`; the brief says to follow the request (teaching from general knowledge, and saying so, when no finding fits) and to keep the leader's questions in order, adding model answers, topics and finding links. Because the agent doesn't reliably add nothing, the app enforces it: a worksheet from the leader's questions is exactly those (their order, kind and words), each given the topic, model answer (the leader's own wins) and finding links of the agent's closest-worded item; the agent's other items are dropped. Text over its limit (model answers 2,500 characters) is cut at a sentence or word with "…". It also passes the topics they already covered (`already_covered`), and the brief asks to build on them rather than repeat them; a correction note overrides the finding. Only those findings are sent. One run on the Super Brain agent (the same agent and model as chat) gets them in a single prompt and returns a `{"worksheets": [...]}` block. The app keeps only:
- the planned researchers, one worksheet each;
- items that are a question or a "try it" step, with a prompt, at most 8 per worksheet;
- finding IDs from what that researcher may learn from.

Text is bounded and secrets are blanked everywhere. A worksheet is seen only by its researcher and the leader, while both are active members of its project. "Try it" steps tell the researcher to use a copy; nothing runs by itself. Tables: `worksheets` and `worksheet_items`, created on start.

### Visualization Studio: graphs of runs and experiments

The dashboard's **Visualize** page ([design](../Documents/visualization-design.md)). A graph is a spec (`backend/visualization/spec.py`: runs, measures, x axis, line or scatter, overlay or separate panels, range, scales, notes, style, units). Every change, from the page or the agent, is a list of operations that `ops.py` checks against the spec and the runs the viewer may see; a refused one changes nothing and says why. `figure.py` draws the Plotly figure from the runs' own numbers (`sources.py`, `transforms.py`). Nothing here writes to runs, alerts or experiments, and none of it calls CREAO except `/agent`.

| Endpoint | Purpose |
|---|---|
| `GET /visualize/datasets?project_id=` | The runs you may graph in the project (`run:<id>`: your own, or the shared ones for the leader; runs with no numbers per step are left out) and your experiments from the local connector (`exp:<id>`, and `base:<set id>` for a set's original run), with measures, alert counts and changed parameters. Also `notes` (for example the connector is down) and an empty `spec` |
| `POST /visualize/figure` | Body: `spec`. The figure, `notes` on what it can't show, `stats` for each line (from every point), `comparisons` (largest difference where runs overlap) and `fingerprints` |
| `POST /visualize/ops` | Body: `spec`, `ops` (up to 50). Applies them in order and draws the result. Adds `applied`, `refused` (`{op, reason}`) and `actions` (`save` or `export`, done by the page) |
| `GET /visualizations?project_id=`, `POST /visualizations` | Your saved graphs (private). Body for saving: `title`, `spec`. Returns `201` |
| `GET`, `PUT`, `DELETE /visualizations/{id}` | Open (redrawn from the current data; a note says which lines changed since it was saved), update (body: `spec`, `ops`, optional `title`; recorded as yours) or delete one |
| `POST /visualizations/{id}/undo` | Back to the spec before the last change (one researcher change, or one agent reply). `409` when there's nothing to undo |
| `POST /visualizations/{id}/agent` | Body: `message`, optional `folder`. Researchers only (`403` for the leader). Starts a reply from your own agent about this graph (`202`, poll `/jobs/{job_id}`); one at a time per project (`409`) |

The agent gets the spec, the catalog (up to 40 runs), each line's statistics and a summary of at most 40 points (`series_summary`), the comparisons and the notes, never a full series. It may use its toolkit, then ends with a `{"graph_ops": [...]}` block instead of a share block. Refused operations go back to it once with the reasons. Applied ones are saved as the agent's, with the last CREAO run ID; the job's result has `reply`, `applied`, `refused`, the redrawn graph and `creao_runs`. Only the researcher sets units (`set_units` from the agent is refused). Phase 2 supports baseline differences, histograms, experiment sweeps, and optional publication PDF export. Full-resolution reads and units remain researcher-only actions. Tables: `visualizations` and `visualization_history` in the researcher's private database (the last 12 exchanges are kept with each graph), created on start; datasets are referenced by fingerprint, never copied.

### Sharing from chat

The group chose to have personal agents share what they establish in chat, without a draft or approval (2026-10-24; diagnoses still need approval). The rules (`share_from_chat` and `add_finding_note` in `backend/knowledge_sharing/publishing.py`):

1. The agent is asked to end each reply with a JSON block, `{"share": ...}`: a title, summary, category, evidence and its own guess when the exchange established a result, a cause, a fix, a measured value or an approach that failed; `null` for greetings, questions and general explanations. It may share only what the user said or the supplied context shows.
2. The app removes the block from the reply. A usable block (title and summary) becomes a shared finding in that project at once, always a **hypothesis** (an agent can't verify anything). An unknown category becomes `research_result`. Evidence goes into the summary, which also says it was shared by the agent and not yet reviewed. The agent's guess is kept apart, in `auto_shares.agent_guess`, never mixed with the evidence. Secrets are blanked everywhere.
3. The reply ends with a plain line naming what was shared, so the researcher knows. The same title from the same researcher in the project isn't shared twice.
4. Findings carry `auto_shared` and `agent_guess`. The dashboard marks them "shared by their agent".
5. Anyone who can read a finding can add **more detail**, **their own guess** or a **correction** (`finding_notes`). Notes go to the Super Brain and to every personal agent with the finding, so a correction reaches the agents' next answers.

If the researcher is removed from the project while the agent is replying, the reply is kept and nothing is shared.

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

SQLite files in `backend/data/` (git ignores it; set `EXPANDRG_DATA_DIR` to move it). They are created with the demo workspace on first use. Installed as a package (`pip install expandrg-<version>.whl`), the demo's files are in `~/.expandrg/demo/data/` and its keys in `~/.expandrg/demo.env` instead, never inside the installed code (`expandrg/paths.py` decides which).

A research group made with `python setup_expandrg.py` or `expandrg setup` (`backend/workspaces/groups.py`) has its own folder. `run_demo.py --group <id>` (or `expandrg start --group <id>`) points the backend at that folder:
- `EXPANDRG_DATA_DIR` is set to the group's `data/`;
- `EXPANDRG_ENV_FILE` is set to its `group.env`, which is read instead of `backend/.env`.

The demo is never added to a database that already holds another group.

| File | Holds |
|---|---|
| `superbrain.db` | The group's shared Super Brain: workspace, members, projects, agents, approvals, shared findings, their links, offboarding handoffs, chat shares (`auto_shares`), members' notes (`finding_notes`), and worksheets with their items, answers and the leader's comments |
| `private/<member>.db` | One researcher's private memory (runs, episodes, drafts for the Super Brain, settings, saved graphs with their history, and saved alerts if they turned history on), split by project. In the product this lives on the researcher's own computer |

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
- **No custom tools or MCP for API runs** (checked 2026-10-24, run `a8d5e5ca`): the agent lists only CREAO's own tools and `connectors: []`, and webhooks to private networks are rejected. So the agents' toolkit works through request blocks ([toolkit](../Documents/agent-toolkit.md)).
- **The agents can run code in CREAO's cloud:** in that run, Agent 1 ran Python (`print(6*7)` → 42) and fetched a public URL (HTTP 200).
- Replies can contain symbols like `α`, which crash `print()` in a Windows terminal. Set `PYTHONIOENCODING=utf-8` before running scripts that print replies.


### Simulation setup and saved local versions

`POST /simulation-setup` accepts `project_id`, a checked project map (`tree`, `files`, `excerpts`, `stats`), `machine` tool availability, and an optional `request`. It runs the signed-in researcher's existing CREAO personal agent and returns a job ID. The completed job contains a validated `plan` with `build_commands`, `run_command`, `working_directory`, cited `source_files`, `environment_notes`, and unresolved `questions`. The setup workflow never executes project code in CREAO or locally. One setup job per member/project runs at a time, and jobs follow current project access.

The local connector adds authenticated `/simulations` routes: `POST /inspect` snapshots a granted existing project and returns its context, accepting optional `environment` (existing environment folder/Python executable) and `compiler` (Fortran executable) paths; `GET /?project_id=...` lists the researcher's versions; `GET /{id}` returns status/output; `GET /{id}/context` rereads the saved snapshot; `POST /{id}/planning` saves/reconnects a setup job; `POST /{id}/plan` checks the plan and local tools; `POST /{id}/environment` connects existing tools or rechecks the current selection with an empty body. Only `POST /{id}/run` executes build/simulation commands. `POST /{id}/stop` is the researcher's explicit stop; `POST /{id}/repeat` prepares another version from retained code/inputs without another CREAO request.

Runtime discovery prefers a project virtual environment, then an activated virtual/Conda environment, then the connector’s current Python. It preserves external interpreter/compiler paths locally and sends CREAO only environment labels, tool/package availability, and compiler aliases. A bounded isolated metadata probe checks the selected interpreter; probe failure means unknown availability. Plans use `{python}`, `{fortran}`, `{cc}`, and `{cxx}` aliases, including Make/CMake compiler arguments. Repeated versions keep the selection, without copying or recreating the environment. A selected folder does not run SDK activation scripts; launch ExpandRG from an activated terminal when those scripts are required.

The connector checks the backend identity and membership before accessing versions, with no client-supplied member identity. Each run registers monitoring before execution. Build steps run sequentially and a failed build never launches the simulation. Output is captured and parsed automatically, and the existing run visibility policy gives the owner and project leader access to its metrics. Source snapshots, code, complete logs, and setup metadata remain on the local computer. The source project is not modified, sensitive files and symlinks are excluded from copies, and each run uses a fresh work copy. Resource limits and command validation do not provide host filesystem/network isolation; the user reviews commands and explicitly clicks Run.


### Visualization Studio phase 2

Graph specs support `baseline`, `difference` (`absolute`/`relative`), `relative_threshold`, `histogram.bins`, `sweep.parameter`/`sweep.step`, and `resolution` (`recorded`/`full`). New operations are `set_baseline`, `configure_histogram`, `configure_sweep`, and researcher-only `set_resolution`; `compare_runs` accepts difference mode. Relative gaps near zero and interpolation are reported explicitly.

`POST /visualize/export` accepts a checked spec, `format` (`pdf`, `svg`, `png`), width/height in inches (2–20), DPI (72–600), font size (6–28), and a supported font family. It returns a file, or 503 with the optional-extra installation command. Matplotlib is an optional `export` extra.

`POST /diagnose` accepts optional `run_id` and `alert_id`. The backend checks run/project visibility, makes a default private diagnostic graph, and passes only bounded summaries to the personal CREAO agent. Valid graph operations attach private visualizations to the result and draft via its existing episode. In the graph-edit conversation, numeric claim references are checked against the supplied statistics and returned as `claim_checks`.

The connector’s read-only `GET /visualize/local-data` resolves exact unambiguous granted log labels or saved simulation outputs, reads project units, and restores at most 100 MB/one million metric points when requested. The backend rejects a full log whose recorded samples no longer match. Experiments expose units from their granted project manifest. No run, alert, or simulation source is modified.
