# CalHacks 2026 — AI Research Platform

An AI research environment where each researcher has a personal AI agent, and all personal agents connect to a central research intelligence agent that holds a project-wide understanding. The website is built for laptops and desktop computers (phones are not a design target).

**Status:** project structure only, no code yet.

## Folder map

| Folder | What goes there |
|---|---|
| `Documents/` | Architecture docs, design mockups, decision records |
| `frontend/` | Desktop-first web app (the researcher-facing website) |
| `backend/` | API layer, supporting services, CREAO integration, database |
| `agents/` | CREAO agent setup: personal agent, hub agent, skills, reference files |
| `shared/` | Definitions used by both frontend and backend (entities, statuses, roles) |
| `infra/` | Hosting, deployment and CI configuration |
| `scripts/` | Setup and developer helper scripts |
| `sample-data/` | Demo project, runs and findings for the hackathon demo |

## Docs

- [Website architecture](Documents/website-architecture.md)
