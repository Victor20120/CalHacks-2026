# frontend

Desktop-first web app for laptop and desktop browsers. See `Documents/website-architecture.md` sections 4–7.

**Framework:** TBD (section 7).

| Folder | Purpose |
|---|---|
| `public/` | Static assets: icons, images, fonts |
| `src/app/` | App entry point, routing, global setup |
| `src/layouts/` | App shell: top bar, left nav, collapsible agent panel |
| `src/pages/` | One folder per page in the site map (see below) |
| `src/components/` | Shared UI pieces: tables, status badges, run cards, charts |
| `src/api/` | Clients that call the backend API |
| `src/state/` | Client-side state (current project, user, notifications) |
| `src/styles/` | Design tokens, light/dark themes, epistemic status colours |
| `src/utils/` | Small helpers (formatting, dates) |
| `tests/` | Frontend tests |

## Pages

| Folder | Page | Maps to |
|---|---|---|
| `dashboard/` | Dashboard | Overview of all systems |
| `agent-workspace/` | Chat with personal agent | Personal research agent |
| `runs/` | Run list, run detail, compare runs | Provenance and run capture |
| `knowledge/` | Findings, failures, decisions, lessons | Knowledge store |
| `cases/` | Diagnosis investigations | Diagnosis engine |
| `approvals/` | Sign-off inbox | Governance |
| `project-map/` | People, workstreams, dependencies | Central (hub) agent |
| `settings/` | Profile, error engine customization, budgets | Governance, diagnosis engine |
