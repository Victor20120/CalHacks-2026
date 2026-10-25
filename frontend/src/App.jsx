import { useEffect, useRef, useState } from "react";
import { backend, remember } from "./api.js";
import DiagnoseView from "./views/DiagnoseView.jsx";
import DraftsView from "./views/DraftsView.jsx";
import ExperimentsView from "./views/ExperimentsView.jsx";
import ProjectView from "./views/ProjectView.jsx";
import OffboardingView from "./views/OffboardingView.jsx";
import RunsView, { isLive } from "./views/RunsView.jsx";
import SimulationsView from "./views/SimulationsView.jsx";
import SignIn from "./views/SignIn.jsx";
import SuperBrainView from "./views/SuperBrainView.jsx";
import { Resizer, useStoredSize } from "./components.jsx";

const VIEWS = [
  ["project", "Project"],
  ["diagnose", "Diagnose"],
  ["drafts", "Drafts"],
  ["superbrain", "Super Brain"],
  ["alerts", "Runs"],
  ["experiments", "Experiments"],
  ["offboarding", "Offboarding"],
];
// The group leader has no personal agent; researchers don't manage membership.
const VIEWS_FOR_ROLE = {
  researcher: ["project", "diagnose", "drafts", "superbrain", "alerts", "experiments"],
  leader: ["superbrain", "alerts", "offboarding"],
};

// ?as=researcher_b&view=alerts&project=heat-diffusion opens the dashboard as a demo
// member (prototype sign-in), e.g. Researcher A and B side by side in two windows.
const START = new URLSearchParams(window.location.search);
const START_VIEW = VIEWS.some(([id]) => id === START.get("view")) ? START.get("view") : "diagnose";

export default function App() {
  const [session, setSession] = useState(null); // { token, member }
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(START.get("project") || remember.get("expandrg.project"));
  const [view, setView] = useState(START_VIEW);
  const [liveCritical, setLiveCritical] = useState(0);
  const [draftCount, setDraftCount] = useState(0);

  async function signIn(memberId) {
    const { token, member } = await backend("/sign-in", { method: "POST", body: { member_id: memberId } });
    const me = await backend("/me", { token });
    remember.set("expandrg.member", memberId);
    setSession({ token, member });
    setProjects(me.projects);
    setProjectId((current) => (me.projects.some((p) => p.id === current) ? current : me.projects[0]?.id || null));
  }

  function signOut() {
    remember.set("expandrg.member", null);
    setSession(null);
  }

  useEffect(() => {
    const requested = START.get("as") || remember.get("expandrg.member");
    if (requested) signIn(requested).catch(() => remember.set("expandrg.member", null));
  }, []);

  useEffect(() => remember.set("expandrg.project", projectId), [projectId]);

  // Critical alerts in runs being watched right now (shown on the Runs tab), and pending drafts.
  useEffect(() => {
    if (!session || !projectId) return undefined;
    let stopped = false;
    async function poll() {
      try {
        const runs = await backend(`/simulation-runs?project_id=${encodeURIComponent(projectId)}`, { token: session.token });
        const drafts = session.member.role === "leader" ? [] : await backend("/drafts", { token: session.token });
        if (!stopped) {
          setLiveCritical(runs.filter(isLive).reduce((sum, r) => sum + r.critical, 0));
          setDraftCount(drafts.length);
        }
      } catch (e) {
        if (e.status === 401) signOut();
      }
    }
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [session, projectId]);

  const [navWidth, setNavWidth] = useStoredSize("expandrg.navWidth");
  const shell = useRef(null);
  const nav = useRef(null);

  if (!session) return <SignIn onSignIn={signIn} />;

  const allowed = VIEWS_FOR_ROLE[session.member.role] || VIEWS_FOR_ROLE.researcher;
  const current = allowed.includes(view) ? view : allowed[0];
  const project = projects.find((p) => p.id === projectId);
  const shared = { session, project, projects };

  return (
    <div className="app" ref={shell} style={navWidth ? { "--nav-width": `${navWidth}px` } : undefined}>
      <div className="narrow-notice">ExpandRG is built for laptop and desktop screens. Widen the window for the best view.</div>
      <header className="topbar">
        <span className="brand" title="Expand Researching Globally">ExpandRG</span>
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
        <span className="member">
          {session.member.display_name}
          <button className="link" onClick={signOut}>
            Switch
          </button>
        </span>
      </header>

      <nav className="sidenav" ref={nav}>
        <Resizer axis="x" label="Sidebar width" current={() => nav.current.offsetWidth} onResize={setNavWidth}
          min={140} max={() => Math.min(420, (shell.current?.offsetWidth || 1200) / 2)} />
        {VIEWS.filter(([id]) => allowed.includes(id)).map(([id, label]) => (
          <button key={id} className={current === id ? "active" : ""} onClick={() => setView(id)}>
            {id === "alerts" ? session.member.role === "leader" ? "Run results" : "Simulations" : label}
            {id === "drafts" && draftCount > 0 && <span className="count">{draftCount}</span>}
            {id === "alerts" && liveCritical > 0 && session.member.role !== "leader" && (
              <span className="count critical">{liveCritical}</span> // problems in your own run that's going on now
            )}
          </button>
        ))}
      </nav>

      <main className={`main ${current === "superbrain" ? "docked" : ""}`}>
        {!project && current !== "offboarding" && <p>You are not a member of any project yet.</p>}
        {project && current === "project" && <ProjectView {...shared} />}
        {project && current === "diagnose" && <DiagnoseView {...shared} onOpenDrafts={() => setView("drafts")} />}
        {project && current === "drafts" && <DraftsView {...shared} />}
        {project && current === "superbrain" && <SuperBrainView {...shared} />}
        {project && current === "alerts" && (session.member.role === "leader"
          ? <RunsView key={project.id} {...shared} />
          : <SimulationsView key={`${session.member.id}:${project.id}`} {...shared} />)}
        {project && current === "experiments" && <ExperimentsView {...shared} />}
        {current === "offboarding" && <OffboardingView {...shared} />}
      </main>
    </div>
  );
}
