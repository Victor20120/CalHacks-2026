# AI Research Platform — Website Architecture

**Status:** Skeleton v0.1 · structure only, sections to be filled in
**Companion to:** `research-platform-architecture.md` (conceptual system architecture)
**Target devices:** Laptops and desktop computers (desktop-first; phones are not a design target)
**Date:** October 2026

---

## 1. Purpose and scope

_TODO: one paragraph on what the website is for._

- The website is the researcher-facing interface to personal agents, the hub agent and the supporting systems (provenance, knowledge, diagnosis, governance).
- **In scope:** _TODO_
- **Out of scope:** native mobile apps, phone-optimized layouts, internal design of the diagnosis engine.

---

## 2. Device and browser targets

| Item | Target |
|---|---|
| Primary devices | Laptop and desktop |
| Design width | 1440px (TBD) |
| Minimum supported width | 1280px (TBD) |
| Browsers | Latest Chrome, Edge, Firefox, Safari (TBD) |
| Input | Mouse or trackpad + keyboard |
| Tablet | TBD |
| Phone | Not supported; show a "best viewed on a laptop or desktop" notice (TBD) |

### 2.1 What desktop-first allows

- Multi-pane layouts (navigation + main view + agent panel side by side).
- Dense tables and side-by-side run comparisons.
- Keyboard shortcuts for common actions.
- Hover states, tooltips and right-click menus.

_TODO: confirm minimum width and whether tablets get a reduced layout._

---

## 3. Users and roles

| Role | What they do on the site | Permissions |
|---|---|---|
| Researcher | _TODO_ | _TODO_ |
| Project lead | _TODO_ | _TODO_ |
| Admin | _TODO_ | _TODO_ |

---

## 4. Site map

```
/
├── Dashboard
├── Agent workspace          (chat with personal agent)
├── Runs                     (provenance explorer)
│   ├── Run detail
│   └── Compare runs
├── Knowledge                (findings, failures, decisions, lessons)
│   └── Entry detail
├── Cases                    (diagnosis investigations)
│   └── Case detail
├── Approvals                (inbox for sign-off requests)
├── Project map              (hub view: people, workstreams, dependencies)
└── Settings
    ├── Profile
    ├── Error engine customization
    └── Budgets and governance
```

_TODO: confirm which pages are MVP (see section 15)._

---

## 5. Layout system

### 5.1 App shell

```
┌──────────────────────────────────────────────────────────────────┐
│  Top bar: project switcher · search · notifications · profile    │
├────────────┬───────────────────────────────────┬─────────────────┤
│            │                                   │                 │
│  Left nav  │           Main view               │  Agent panel    │
│            │                                   │  (collapsible)  │
│            │                                   │                 │
└────────────┴───────────────────────────────────┴─────────────────┘
```

### 5.2 Breakpoints

| Name | Width | Layout behaviour |
|---|---|---|
| Large desktop | ≥ 1600px | _TODO_ |
| Desktop / laptop | 1280–1599px | _TODO_ |
| Below minimum | < 1280px | _TODO_ |

### 5.3 Design system

- Typography: _TODO_
- Colour and theming (light / dark): _TODO_
- Status colours for epistemic status (observed, hypothesized, supported, verified, refuted, superseded): _TODO_
- Core components: _TODO_

---

## 6. Pages and views

> Template for each page: **Purpose · Key components · Data shown · Actions · Maps to (system component)**

### 6.1 Dashboard
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: _TODO_

### 6.2 Agent workspace
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Personal research agent

### 6.3 Runs (provenance explorer)
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Provenance and run capture

### 6.4 Knowledge
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Knowledge store

### 6.5 Cases
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Diagnosis engine

### 6.6 Approvals
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Governance

### 6.7 Project map
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Central research intelligence agent (hub)

### 6.8 Settings
- Purpose: _TODO_
- Key components: _TODO_
- Data shown: _TODO_
- Actions: _TODO_
- Maps to: Governance, diagnosis engine customization

---

## 7. Frontend architecture

| Decision | Options considered | Choice | Notes |
|---|---|---|---|
| Framework | _TODO_ | TBD | |
| Routing | _TODO_ | TBD | |
| State management | _TODO_ | TBD | |
| Component library | _TODO_ | TBD | |
| Styling | _TODO_ | TBD | |
| Charts / data viz | _TODO_ | TBD | |
| Real-time updates | _TODO_ | TBD | Agent streaming, notifications |

---

## 8. Backend and integrations

### 8.1 High-level diagram

```
 ┌──────────────────────┐
 │   Browser (desktop)  │
 └──────────┬───────────┘
            │
 ┌──────────┴───────────┐
 │  Web app / API layer │
 └──┬───────┬───────┬───┴──────────┬──────────────┐
    │       │       │              │              │
 Provenance Knowledge Diagnosis  Governance    CREAO AI
  service   store    service     service     (agents, via
                                              API + webhooks)
```

### 8.2 API layer
- Style (REST / GraphQL / other): _TODO_
- Versioning: _TODO_

### 8.3 CREAO integration
- Starting agent runs (API trigger): _TODO_
- Receiving results (completion webhooks): _TODO_
- Streaming agent replies into the Agent workspace: _TODO_

### 8.4 Research sources
- Code, configs, simulation outputs, notes: _TODO_

---

## 9. Key data flows

| Flow | Steps | Notes |
|---|---|---|
| Researcher chats with personal agent | _TODO_ | |
| Run completes and appears on dashboard | _TODO_ | |
| Approval requested and decided | _TODO_ | |
| Finding published and hub notifies others | _TODO_ | |

---

## 10. Authentication and authorization

- Sign-in method: _TODO_
- Session handling: _TODO_
- Role-based access (per section 3): _TODO_
- Personal vs project vs organization scope enforcement: _TODO_

---

## 11. Notifications and real-time

- In-app notifications: _TODO_
- Alert routing options (silent log, daily digest, immediate): _TODO_
- Email / Slack: _TODO_

---

## 12. Non-functional requirements

| Area | Requirement |
|---|---|
| Performance | _TODO_ |
| Accessibility | _TODO_ (keyboard navigation, contrast, screen readers) |
| Security | _TODO_ |
| Privacy | _TODO_ |
| Reliability | _TODO_ |
| Audit logging | _TODO_ |

---

## 13. Hosting and deployment

- Hosting: _TODO_
- Environments (dev / staging / prod): _TODO_
- CI/CD: _TODO_
- Secrets management: _TODO_

---

## 14. Repository structure

Each top-level folder has a README describing its subfolders.

```
CalHacks-2026/
├── Documents/                  architecture docs
│   ├── design/                 wireframes and mockups
│   └── decisions/              decision records
├── frontend/                   desktop-first web app
│   ├── public/                 icons, images, fonts
│   ├── src/
│   │   ├── app/                entry point, routing
│   │   ├── layouts/            app shell (top bar, left nav, agent panel)
│   │   ├── pages/              one folder per page in section 4
│   │   ├── components/         shared UI pieces
│   │   ├── api/                backend API clients
│   │   ├── state/              client-side state
│   │   ├── styles/             design tokens, themes
│   │   └── utils/
│   └── tests/
├── backend/
│   ├── api/                    routes, middleware
│   ├── services/               provenance, knowledge, diagnosis, governance, notifications
│   ├── integrations/           creao (webhooks, mcp-server), research-sources
│   ├── database/               schema, migrations, seeds
│   ├── config/
│   └── tests/
├── agents/                     CREAO setup: personal-agent, hub-agent, skills, reference-files
├── shared/                     schemas, constants used by frontend and backend
├── infra/                      hosting, deployment, CI
├── scripts/                    setup and dev helpers
└── sample-data/                demo data for the hackathon
```

---

## 15. Roadmap

### MVP (hackathon)
- _TODO: which pages and flows must work for the demo_

### Later
- _TODO_

---

## 16. Open questions

| Question | Owner | Status |
|---|---|---|
| Minimum supported screen width? | _TODO_ | Open |
| Do tablets need a reduced layout? | _TODO_ | Open |
| _TODO_ | | |
