# backend

API layer and the supporting systems we own. CREAO is the agent runtime, not the system of record. See `Documents/website-architecture.md` section 8.

**Language / framework:** TBD.

| Folder | Purpose |
|---|---|
| `api/routes/` | Endpoints the website calls, grouped by page or service |
| `api/middleware/` | Sign-in checks, permissions, request logging |
| `services/provenance/` | Records every run with code version, config, inputs, environment, who and why |
| `services/knowledge/` | Findings, failures, decisions and lessons, each with an epistemic status |
| `services/diagnosis/detectors/` | Deterministic checks that flag problems (design deferred) |
| `services/diagnosis/triage/` | Rules that sort flags into likely error, anomaly, or possibly real result |
| `services/diagnosis/detector-packs/` | Add-on check sets per research field |
| `services/governance/` | Roles, approval gates, budgets, audit trail |
| `services/notifications/` | In-app alerts, digests, email/Slack routing |
| `integrations/creao/webhooks/` | Receives CREAO completion callbacks |
| `integrations/creao/mcp-server/` | Our diagnosis service exposed to CREAO agents as a custom MCP server |
| `integrations/research-sources/` | Connections to code repos, compute and simulation outputs |
| `database/schema/` | Data model for the core entities |
| `database/migrations/` | Schema changes over time |
| `database/seeds/` | Starter data for local development |
| `config/` | Per-environment settings (secrets stay in `.env`, never committed) |
| `tests/` | Backend tests |
