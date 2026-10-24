# frontend

React dashboard (Vite), built for laptop and desktop browsers. Views: Diagnose, Drafts, Super Brain and Alerts. Design notes are in [dashboard-design.md](../Documents/dashboard-design.md).

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
| `src/App.jsx` | App shell: top bar, navigation, alerts panel, sign-in state, polling |
| `src/api.js` | Calls to the backend and the connector |
| `src/components.jsx` | Badges, the agent-reply renderer, the folder picker |
| `src/views/` | One file per view |
| `src/styles.css` | Layout, light and dark themes, severity and verification styles |
