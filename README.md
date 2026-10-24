# SimGuard

A collaborative AI research assistant for research groups that build custom numerical solvers. Each researcher has a personal agent that helps them understand their code, monitor simulation runs and investigate bugs. Findings a researcher approves go into the group's private shared knowledge base, the **Super Brain**, so the next researcher who hits a related problem can find them.

**Status:** prototype, early setup. The dashboard is built for laptops and desktop computers.

## Docs

- [Build plan](Documents/build-plan.md): ordered steps for the first milestone, with a checkpoint test after each
- [Dashboard design](Documents/dashboard-design.md): desktop layout and views

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
2. Install the two packages the CREAO test uses: `pip install requests python-dotenv`
3. Check that CREAO works (this starts a real run and uses credits): `cd backend` then `python testcreo.py`
