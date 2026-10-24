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
| `POST /diagnose` | Signed in. Body: `project_id` (one of your projects), `symptom`, `focus_areas` (list; default `["numerical"]`), `analysis_depth` (default `standard`), `evidence` (list of `path`, `kind`, `excerpt`, `truncated`). Checks the evidence, starts a run on **your own** personal agent and returns `202` with `job_id`, `evidence_sent` and `redactions` |
| `GET /diagnose/{job_id}` | Signed in, your own jobs only. `status` is `running`, `completed` (with `diagnosis`: `reply`, `issues`, `summary`, `run_id`; and `draft_id`) or `failed` (with `error`: `type`, `message`, sometimes `run_id`). A completed diagnosis is saved to your private memory |
| `GET /drafts` | Your pending drafts for the Super Brain. Only you can see them |
| `POST /drafts/{draft_id}/approve` | Publish a draft as a shared finding. Optional edits: `title`, `summary`, `category`, `verification_status` |
| `POST /drafts/{draft_id}/reject` | Keep a draft private for good. Nothing is written to the shared database |
| `GET /projects/{project_id}/findings` | Approved findings in a project you belong to |

## Databases

SQLite files in `backend/data/` (git ignores it; set `SIMGUARD_DATA_DIR` to move it). They are created with the demo workspace on first use.

| File | Holds |
|---|---|
| `superbrain.db` | The group's shared Super Brain: workspace, members, projects, agents, approvals, shared findings and their links |
| `private/<member>.db` | One researcher's private memory (runs, episodes and drafts for the Super Brain), split by project. In the product this lives on the researcher's own computer |

Private and shared data are in different files with different tables, so no shared query can return private memory. `approvals` stores no draft text: rejected drafts never reach the shared database. Approving writes the approval and the finding in one transaction.

When the tables change, `SCHEMA_VERSION` in `backend/db/schema.py` goes up and older database files are refused with instructions to reset them (there are no migrations in the prototype).

Demo workspace: a group leader, Researcher A (projects `heat-diffusion` and `wave-propagation`) and Researcher B (`heat-diffusion` only). Delete everything and reload it with:

```
python -m backend.workspaces.demo --reset
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
- **Keys aren't limited to one agent in practice:** each agent has its own `capi_` key, but any of our keys can list and read the other agents' runs. CREAO's docs say a key is scoped to one agent. So SimGuard must choose each member's app ID in its own code. CREAO keeps agent memory per app ID, not per key.
- **Agent output:** `result.chatText` is the prose reply. `result.text` is a JSON string whose shape depends on the outcome: `{"response": ...}` when inputs were missing, or the inputs echoed back plus summary counts (`issues_confirmed`, `issues_suspected`, `blocking_count`, `reproducibility_score`, `experiments_proposed`) when the analysis ran. Agents can also return counts when nothing was analyzed (all zeros, with a `reproducibility_score` of `0` or `"not_computed"`). The reliable signal is the echoed `analysis_depth`: a real depth (`triage`, `standard`, `deep`) on real analyses, `none` or `not_run` otherwise. The adapter sets `analysis_ran` from that.
- **Issue list:** the agent reads inputs it doesn't declare. `hub_agent()` sends an extra `output_format` input with every run, asking the agent to end its reply with a JSON block listing each issue. The adapter moves that block into `result.issues` (verified on run `790fe5fd`: 6 issues matching the summary counts).
- A full diagnosis took about 60 seconds. The response includes no credit usage and no artifacts.
- Known-good example run: `tests/fixtures/creao_hub_run_completed.json` (app ID redacted).
- CREAO runs in the cloud and cannot read local folders. Send evidence text, not a local path.
- Replies can contain symbols like `α`, which crash `print()` in a Windows terminal. Set `PYTHONIOENCODING=utf-8` before running scripts that print replies.
