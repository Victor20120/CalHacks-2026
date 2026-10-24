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

Errors are subclasses of `CreaoError`: `CreaoInputError` (nothing sent), `CreaoRequestError` (network, bad key, rate limit), `CreaoRunFailed`, `CreaoTimeout` (has the `run_id` to check later).

## Running the backend

```
python -m uvicorn backend.api.app:app --port 8000
```

It listens on this computer only (`127.0.0.1`). There is no sign-in yet; workspace and project permissions come in build step 3.

| Endpoint | What it does |
|---|---|
| `GET /health` | Returns `{"status": "ok"}` |
| `POST /diagnose` | Body: `project` (a name, not a path), `symptom`, optional `focus_areas`, `evidence` (list of `path`, `kind`, `excerpt`, `truncated`). Checks the evidence, starts a CREAO run and returns `202` with `job_id`, `evidence_sent` and `redactions` |
| `GET /diagnose/{job_id}` | `status` is `running`, `completed` (with `diagnosis`: `reply`, `issues`, `summary`, `run_id`) or `failed` (with `error`: `type`, `message`, sometimes `run_id`) |

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
| `GET /apps/{appId}/runs` | Manual check, 2026-10-23 | Lists past runs. Uses no credits |

- **Hub agent inputs:** `project_path`, `observed_symptom` and `focus_areas` are required. `analysis_depth` is optional and defaults to `standard`. A run without the required inputs completes but only reports what's missing.
- **Hub agent output:** `result.chatText` is the prose reply. `result.text` is a JSON string whose shape depends on the outcome: `{"response": ...}` when inputs were missing, or the inputs echoed back plus summary counts (`issues_confirmed`, `issues_suspected`, `blocking_count`, `reproducibility_score`, `experiments_proposed`) when the analysis ran.
- **Issue list:** the agent reads inputs it doesn't declare. `hub_agent()` sends an extra `output_format` input with every run, asking the agent to end its reply with a JSON block listing each issue. The adapter moves that block into `result.issues` (verified on run `790fe5fd`: 6 issues matching the summary counts).
- A full diagnosis took about 60 seconds. The response includes no credit usage and no artifacts.
- Known-good example run: `tests/fixtures/creao_hub_run_completed.json` (app ID redacted).
- CREAO runs in the cloud and cannot read local folders. Send evidence text, not a local path.
- Replies can contain symbols like `α`, which crash `print()` in a Windows terminal. Set `PYTHONIOENCODING=utf-8` before running scripts that print replies.
