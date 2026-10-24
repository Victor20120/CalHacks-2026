# SimGuard — Dashboard Design

**Status:** Skeleton v0.2 · UI only.
**Target devices:** Laptops and desktop computers (desktop-first; phones are not a design target)
**Date:** October 2026

---

## 1. Scope

- The React dashboard used by researchers and the group leader.
- **V1 includes:** workspace/project selection, diagnoses, severity alerts, approvals, Super Brain search, a few settings, and (optional) the offboarding demo.
- **Not in V1:** email or desktop notifications (alerts appear only in the dashboard), real-time streaming, onboarding curriculum UI, phone layouts.

---

## 2. Device and browser targets

| Item | Target |
|---|---|
| Primary devices | Laptop and desktop |
| Design width | 1440px (TBD) |
| Minimum supported width | 1280px (TBD) |
| Browsers | Latest Chrome, Edge, Firefox, Safari (TBD) |
| Input | Mouse or trackpad + keyboard |
| Phone | Not supported; show a "best viewed on a laptop or desktop" notice |

Desktop-first allows multi-pane layouts, side-by-side evidence and diagnosis, keyboard shortcuts, and hover tooltips.

---

## 3. Demo users

| User | Role in the demo | Can see |
|---|---|---|
| Researcher A | Hits the first simulation issue and approves sharing the finding | _TODO: projects_ |
| Researcher B | Hits a related issue and gets A's finding from the Super Brain | _TODO: projects (must exclude one of A's projects, for the isolation test)_ |
| Group leader | Starts the offboarding demo (optional) | _TODO_ |

---

## 4. Views

```
Dashboard
├── Workspace & project picker   (+ demo researcher switcher)
├── Diagnosis                    (agent conversation, evidence, proposed fix)
├── Alerts                       (Informational / Warning / Critical)
├── Approvals                    (knowledge contributions, proposed code edits)
├── Super Brain                  ("Ask Super Brain" search, shared findings)
├── Settings                     (alert history saving, automatic Super Brain search)
└── Offboarding                  (leader only, optional)
```

Built so far: researchers get Diagnose, Drafts, Super Brain and Alerts; the group leader (who has no personal agent) gets Super Brain, Alerts and Offboarding. The two settings live where they're used: "Save alert history" in Alerts, and "Give my agent relevant findings from the Super Brain" in Diagnose.

> Template for each view: **Purpose · Shows · Actions · Rules**

### 4.1 Workspace & project picker
- Purpose: _TODO_
- Shows: _TODO_
- Actions: _TODO_
- Rules: researchers only see projects they're members of.

### 4.2 Diagnosis
- Purpose: _TODO_
- Shows: observed symptoms, retrieved prior evidence, hypothesis, confidence, proposed validation steps, each labelled separately
- Data available now from the CREAO adapter: the prose reply, summary counts, and an issue list (title, agent status, confidence, evidence, proposed validation)
- Actions: _TODO_
- Rules: a proposed code edit is never applied without approval.

### 4.3 Alerts
- Purpose: _TODO_
- Shows: _TODO_
- Actions: _TODO_
- Rules: alerts never pause or stop the simulation. Without alert saving turned on, alerts last only for the session.

### 4.4 Approvals
- Purpose: _TODO_
- Shows: the agent's editable draft (research result, code bug / new implementation, or error diagnosis)
- Actions: approve, edit, reject
- Rules: nothing reaches the Super Brain without approval.

### 4.5 Super Brain
- Purpose: _TODO_
- Shows: _TODO_
- Actions: "Ask Super Brain" search (always available)
- Rules: results respect project access; hypotheses never look like verified facts.

### 4.6 Settings
- Save alert history: **off** by default
- Automatic Super Brain search: **on** by default
- _TODO_

### 4.7 Offboarding (optional, step 7)
- Purpose: _TODO_
- Shows: handoff progress: revoke → prepare → transfer → verify → deactivate
- Rules: report a failed handoff honestly; never show "complete" unless verified.

---

## 5. Layout

### 5.1 App shell

```
┌──────────────────────────────────────────────────────────────────┐
│  Top bar: workspace · project · researcher · Ask Super Brain     │
├────────────┬───────────────────────────────────┬─────────────────┤
│            │                                   │                 │
│  Left nav  │           Main view               │  Alerts panel   │
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

### 5.3 Visual language

| Thing | Needs a distinct look | Notes |
|---|---|---|
| Alert severity | Informational, Warning, Critical | _TODO: colours_ |
| Verification status | Hypothesis, supported, verified, unresolved | A hypothesis must never look like a fact |
| Agent's issue status | Agent-confirmed vs suspected | Agent-confirmed is still not researcher-verified; style it differently from verified |
| Private vs shared | Private project memory vs Super Brain finding | _TODO_ |

---

## 6. Frontend decisions

| Decision | Choice | Notes |
|---|---|---|
| Framework | React 18 | Decided |
| Getting updates | Polling | Alerts and drafts every 3 s, watches every 2 s, diagnosis jobs every 3 s. No real-time streaming in V1 |
| Build tool | Vite | Its dev server also passes `/api` to the backend and `/connector` to the local connector |
| Routing | None | Views switch inside the app; `?as=`, `&view=` and `&project=` open a given member and view |
| Styling / components | Plain CSS, no component library | Light and dark themes follow the system setting |

---

## 7. Open questions

| Question | Owner | Status |
|---|---|---|
| Minimum supported screen width? | _TODO_ | Open |
| Which projects does each demo researcher belong to? | _TODO_ | Open |
| _TODO_ | | |
