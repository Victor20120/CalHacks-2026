import { useEffect, useState } from "react";
import { backend, remember } from "./api.js";
import { SeverityBadge } from "./components.jsx";
import AlertsView from "./views/AlertsView.jsx";
import DiagnoseView from "./views/DiagnoseView.jsx";
import DraftsView from "./views/DraftsView.jsx";
import SignIn from "./views/SignIn.jsx";
import SuperBrainView from "./views/SuperBrainView.jsx";

const VIEWS = [
  ["diagnose", "Diagnose"],
  ["drafts", "Drafts"],
  ["superbrain", "Super Brain"],
  ["alerts", "Alerts"],
];

// ?as=researcher_b&view=alerts&project=heat-diffusion opens the dashboard as a demo
// member (prototype sign-in), e.g. Researcher A and B side by side in two windows.
const START = new URLSearchParams(window.location.search);
const START_VIEW = VIEWS.some(([id]) => id === START.get("view")) ? START.get("view") : "diagnose";

export default function App() {
  const [session, setSession] = useState(null); // { token, member }
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(START.get("project") || remember.get("simguard.project"));
  const [view, setView] = useState(START_VIEW);
  const [alerts, setAlerts] = useState([]);
  const [draftCount, setDraftCount] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [panelOpen, setPanelOpen] = useState(true);

  async function signIn(memberId) {
    const { token, member } = await backend("/sign-in", { method: "POST", body: { member_id: memberId } });
    const me = await backend("/me", { token });
    remember.set("simguard.member", memberId);
    setSession({ token, member });
    setProjects(me.projects);
    setProjectId((current) => (me.projects.some((p) => p.id === current) ? current : me.projects[0]?.id || null));
  }

  function signOut() {
    remember.set("simguard.member", null);
    setSession(null);
    setAlerts([]);
  }

  useEffect(() => {
    const requested = START.get("as") || remember.get("simguard.member");
    if (requested) signIn(requested).catch(() => remember.set("simguard.member", null));
  }, []);

  useEffect(() => remember.set("simguard.project", projectId), [projectId]);

  // Poll this researcher's alerts and pending drafts.
  useEffect(() => {
    if (!session) return undefined;
    let stopped = false;
    async function poll() {
      try {
        const [latest, drafts] = await Promise.all([
          backend("/alerts", { token: session.token }),
          backend("/drafts", { token: session.token }),
        ]);
        if (!stopped) {
          setAlerts(latest);
          setDraftCount(drafts.length);
        }
      } catch (e) {
        if (e.status === 401 || e.status === 403) signOut();
      }
    }
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [session]);

  if (!session) return <SignIn onSignIn={signIn} />;

  const project = projects.find((p) => p.id === projectId);
  const criticalCount = alerts.filter((a) => a.severity === "critical").length;
  const shared = { session, project, projects };

  function ask(event) {
    event.preventDefault();
    setView("superbrain");
  }

  return (
    <div className={`app ${panelOpen ? "" : "panel-closed"}`}>
      <div className="narrow-notice">SimGuard is built for laptop and desktop screens. Widen the window for the best view.</div>
      <header className="topbar">
        <span className="brand">SimGuard</span>
        <label className="project-select">
          Project
          <select value={projectId || ""} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <form className="ask" onSubmit={ask}>
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Ask Super Brain, e.g. blows up after refining the grid"
          />
          <button type="submit" disabled={searchQuery.trim().length < 2}>
            Ask
          </button>
        </form>
        <span className="member">
          {session.member.display_name}
          <button className="link" onClick={signOut}>
            Switch
          </button>
        </span>
      </header>

      <nav className="sidenav">
        {VIEWS.map(([id, label]) => (
          <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>
            {label}
            {id === "drafts" && draftCount > 0 && <span className="count">{draftCount}</span>}
            {id === "alerts" && criticalCount > 0 && <span className="count critical">{criticalCount}</span>}
          </button>
        ))}
      </nav>

      <main className="main">
        {!project && <p>You are not a member of any project yet.</p>}
        {project && view === "diagnose" && <DiagnoseView {...shared} onOpenDrafts={() => setView("drafts")} />}
        {project && view === "drafts" && <DraftsView {...shared} />}
        {project && view === "superbrain" && <SuperBrainView {...shared} query={searchQuery} onQuery={setSearchQuery} />}
        {project && view === "alerts" && <AlertsView {...shared} alerts={alerts} onCleared={() => setAlerts([])} />}
      </main>

      <aside className="alerts-panel">
        <button className="panel-toggle" onClick={() => setPanelOpen(!panelOpen)}>
          {panelOpen ? "Hide alerts ›" : "‹ Alerts"}
        </button>
        {panelOpen && (
          <>
            <h3>Recent alerts</h3>
            <p className="muted small">Alerts never stop your simulation. They are not saved.</p>
            {!alerts.length && <p className="muted">No alerts this session.</p>}
            {alerts.slice(0, 8).map((a) => (
              <div key={a.id} className={`alert-card severity-border-${a.severity}`}>
                <SeverityBadge severity={a.severity} /> <span className="muted small">{a.run_label}</span>
                <p>{a.message}</p>
              </div>
            ))}
          </>
        )}
      </aside>
    </div>
  );
}
