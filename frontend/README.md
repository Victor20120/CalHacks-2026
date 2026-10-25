# frontend

React dashboard (Vite), built for laptop and desktop browsers. A researcher has Project, Diagnose, Drafts, Super Brain, Simulations and Visualize; the group leader has Super Brain, Run results, Visualize, Projects and Offboarding. Design notes are in [dashboard-design.md](../Documents/dashboard-design.md).

```
npm install
npm run dev        # http://127.0.0.1:5173
npm run build      # production build in dist/
```

The dev server passes requests on to the two local services, so they must be running too:

| Path | Goes to | Start it with |
|---|---|---|
| `/api` | The backend, `127.0.0.1:8000` | `python -m uvicorn backend.api.app:app --port 8000` |
| `/connector` | The local connector, `127.0.0.1:8001` | `python -m local_connector serve` |

The browser never reads local files itself: it asks the local connector, which applies the folder grants and evidence rules.

`?as=researcher_b&view=alerts&project=heat-diffusion` opens the dashboard signed in as a demo member on a given view (prototype sign-in), for example to show Researcher A and B side by side.

| File | What it is |
|---|---|
| `src/App.jsx` | App shell: the sidebar (project switcher, grouped navigation, member menu), the top bar (breadcrumbs, light/dark toggle), sign-in state, polling |
| `src/api.js` | Calls to the backend and the connector |
| `src/components.jsx` | Badges, the agent-reply renderer, the folder picker, avatars, menus, resize handles and the theme toggle |
| `src/charts.jsx` | Line charts of a run (one per measure, log scale for blow-ups, alert markers, hover, replay) and the same numbers as a table |
| `src/views/` | One file per view. `LessonsView.jsx` is Super Brain → Lessons: it loads KaTeX (its own file) only when opened to draw the lessons' equations, and draws their pictures (flows, grids, computed plots) itself. `VisualizeView.jsx` is the Visualization Studio: it loads Plotly (`plotly.js-dist-min`, its own file) only when opened, draws the figure the backend built in the page's theme, and sends every change as operations to `/visualize/ops` |
| `src/styles.css` | The design: colour tokens for light and dark (including `--slot-0` to `--slot-7`, the graph palette), the shell, cards, buttons, badges, tables, charts; severity and verification styles |

Icons are Lucide (`lucide-react`); text is Inter and numbers and paths JetBrains Mono, loaded from Google Fonts in `index.html` (system fonts when offline).

## Super Brain conversation

`src/AgentChat.jsx` is the "Ask Super Brain" panel beside the updates: one conversation with the CREAO hub, which also draws on what the group shared. It starts on the chosen project; the Knowledge selector widens it to All my projects. Your messages are on the right, its answers on the left. There is no response-mode selector or local-answer fallback.

Every submission goes to `POST /superbrain/chat`, which returns a `job_id` (`202`). The panel polls every 750 ms, shows "Super Brain is thinking… Ns" while the hub works, and then the answer, labelled "Super Brain · CREAO". Response instructions (the sliders button) are saved before the hub call and kept in sync with the editor. Each user/project/knowledge scope has its own conversation; errors keep the message for retry. Clear chat keeps instructions. The footer says that sending uses CREAO credits. The panel's left edge resizes it.
