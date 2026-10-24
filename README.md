# SimGuard

A collaborative AI research assistant for research groups that build custom numerical solvers. Each researcher has a personal agent that helps them understand their code, monitor simulation runs and investigate bugs. Findings a researcher approves go into the group's private shared knowledge base, the **Super Brain**, so the next researcher who hits a related problem can find them.

**Status:** prototype, early setup. The dashboard is built for laptops and desktop computers.

## Docs

- [Build plan](Documents/build-plan.md): ordered steps for the first milestone, with a checkpoint test after each
- [Dashboard design](Documents/dashboard-design.md): desktop layout and views
- [Changelog](CHANGELOG.md): what each commit changed

## Folder map

| Folder | What goes there |
|---|---|
| `frontend/` | React dashboard |
| `backend/` | FastAPI backend: permissions, CREAO client, memory, monitoring, knowledge sharing, SQLite |
| `local_connector/` | Python process on the researcher's computer: authorized file access, evidence extraction, simulation monitoring |
| `tests/` | Automated tests (CREAO is mocked, so tests use no credits) |
| `Documents/` | Build plan and dashboard design |

## Setup so far

1. Copy `backend/.env.example` to `backend/.env` and fill in the CREAO keys and app IDs. Never commit `.env`.
2. Install the Python packages: `python -m pip install -r backend/requirements.txt`
3. Run the tests from the repo root (no network, no credits): `python -m pytest`
4. Check that CREAO works (this starts a real run and uses credits): `cd backend` then `python testcreo.py`

## Try a diagnosis

From the repo root, in two terminals. The diagnosis starts a real CREAO run and uses credits.

```
# Terminal 1: start the backend
python -m uvicorn backend.api.app:app --port 8000

# Terminal 2: allow SimGuard to read the sample project, then diagnose it
python -m local_connector grant tests/fixtures/sample_project
python -m local_connector diagnose tests/fixtures/sample_project --symptom "max|T| reaches inf at step 2223" --file run.log --file config.json --file heat2d.py:16-23
```

The connector shows exactly what will be sent and asks before sending. Granted folders are remembered in `~/.simguard/grants.json`; take access back with `python -m local_connector revoke <folder>`.
