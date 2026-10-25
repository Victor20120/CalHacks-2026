import { useEffect, useRef, useState } from "react";
import {
  BrainCircuit, ChartSpline, Check, ChevronsUpDown, Crown, FilePenLine, FlaskConical, FolderKanban, GitCompare, LayoutDashboard,
  LineChart, LogOut, Microscope, Moon, Network, PanelLeft, PlayCircle, Plus, Stethoscope, Sun, UserMinus,
} from "lucide-react";
import { backend, remember } from "./api.js";
import DiagnoseView from "./views/DiagnoseView.jsx";
import DraftsView from "./views/DraftsView.jsx";
import ExperimentsView from "./views/ExperimentsView.jsx";
import ProjectView from "./views/ProjectView.jsx";
import ProjectsView from "./views/ProjectsView.jsx";
import OffboardingView from "./views/OffboardingView.jsx";
import RunsView, { isLive } from "./views/RunsView.jsx";
import SimulationsView from "./views/SimulationsView.jsx";
import SignIn from "./views/SignIn.jsx";
import SuperBrainView from "./views/SuperBrainView.jsx";
import VisualizeView from "./views/VisualizeView.jsx";
import { Avatar, Menu, Resizer, useStoredSize, useTheme } from "./components.jsx";

// The sidebar's groups: a researcher's own work, the group's knowledge, simulations, and
// (the group leader only) running the group. The leader has no personal agent; researchers
// don't manage projects or membership. "alerts" is Simulations for a researcher and Run
// results for the leader (the ID stays, so ?view=alerts links keep working).
const NAV = [
  { group: "Research", role: "researcher", items: [
    { id: "project", label: "Project", icon: LayoutDashboard },
    { id: "diagnose", label: "Diagnose", icon: Stethoscope },
    { id: "drafts", label: "Drafts", icon: FilePenLine },
  ] },
  { group: "Knowledge", items: [
    { id: "superbrain", label: "Super Brain", icon: BrainCircuit, sub: [["updates", "Updates"], ["worksheets", "Worksheets"]] },
  ] },
  { group: "Simulation", role: "researcher", items: [
    { id: "alerts", label: "Simulations", icon: PlayCircle, sub: [["setup", "Set up & run"], ["monitor", "Monitor"]] },
    { id: "visualize", label: "Visualize", icon: ChartSpline },
    { id: "experiments", label: "Experiments", icon: GitCompare },
  ] },
  { group: "Group", role: "leader", items: [
    { id: "alerts", label: "Run results", icon: LineChart },
    { id: "visualize", label: "Visualize", icon: ChartSpline },
    { id: "projects", label: "Projects", icon: FolderKanban },
    { id: "offboarding", label: "Offboarding", icon: UserMinus },
  ] },
];
const VIEWS = ["project", "diagnose", "drafts", "superbrain", "alerts", "visualize", "experiments", "projects", "offboarding"];
const WITHOUT_PROJECT = ["projects", "offboarding"]; // pages that don't need a project chosen

// ?as=researcher_b&view=alerts&project=heat-diffusion opens the dashboard as a demo
// member (prototype sign-in), e.g. Researcher A and B side by side in two windows.
const START = new URLSearchParams(window.location.search);
const START_VIEW = VIEWS.includes(START.get("view")) ? START.get("view") : "diagnose";

export default function App() {
  const [session, setSession] = useState(null); // { token, member }
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(START.get("project") || remember.get("expandrg.project"));
  const [view, setView] = useState(START_VIEW);
  const [tabs, setTabs] = useState({ superbrain: START.get("tab") === "worksheets" ? "worksheets" : "updates", alerts: "setup" });
  const [liveCritical, setLiveCritical] = useState(0);
  const [draftCount, setDraftCount] = useState(0);
  const [diagnosticSeed, setDiagnosticSeed] = useState(null);
  const [graphSeed, setGraphSeed] = useState(null); // runs to open in Visualize: { datasets, metrics, agent, at }
  const [menu, setMenu] = useState(null); // { kind: "project" | "person", anchor }
  const [drawer, setDrawer] = useState(false); // the sidebar on narrow screens
  const [theme, toggleTheme] = useTheme();

  async function signIn(memberId) {
    const { token, member } = await backend("/sign-in", { method: "POST", body: { member_id: memberId } });
    const me = await backend("/me", { token });
    remember.set("expandrg.member", memberId);
    setSession({ token, member });
    setProjects(me.projects);
    setProjectId((current) => (me.projects.some((p) => p.id === current) ? current : me.projects[0]?.id || null));
  }

  // After the leader creates a project: list it and switch to it.
  async function projectCreated(id) {
    const me = await backend("/me", { token: session.token });
    setProjects(me.projects);
    setProjectId(id);
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

  // Critical alerts in runs being watched right now (shown on the Simulations item), and pending drafts.
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
  const nav = useRef(null);

  if (!session) return <SignIn onSignIn={signIn} />;

  const role = session.member.role === "leader" ? "leader" : "researcher";
  const groups = NAV.filter((g) => !g.role || g.role === role);
  const allowed = groups.flatMap((g) => g.items.map((i) => i.id));
  const current = allowed.includes(view) ? view : allowed[0];
  const currentItem = groups.flatMap((g) => g.items).find((i) => i.id === current);
  const project = projects.find((p) => p.id === projectId);
  const shared = { session, project, projects };
  const open = (id) => { setView(id); setGraphSeed(null); setDiagnosticSeed(null); setDrawer(false); window.scrollTo({ top: 0 }); };
  const openTab = (id, tab) => { setTabs({ ...tabs, [id]: tab }); open(id); };
  // "Open in Visualize" and "Edit with Agent" on a run or an experiment comparison.
  const visualize = (datasets, agent = false) => { open("visualize"); setGraphSeed({ datasets, agent, at: Date.now() }); };
  const diagnose = (seed) => { open("diagnose"); setDiagnosticSeed({ ...seed, project_id: projectId, at: Date.now() }); };
  const openDiagnostic = (graph, graphProject = projectId) => {
    if (graphProject) setProjectId(graphProject);
    open("visualize"); setGraphSeed({ graph_id: graph.id, at: Date.now() });
  };
  const counts = { drafts: draftCount, alerts: role === "researcher" ? liveCritical : 0 };

  return (
    <div className="app" style={navWidth ? { "--sidebar-w": `${navWidth}px` } : undefined}>
      <aside className={`sidebar ${drawer ? "open" : ""}`} ref={nav}>
        <Resizer axis="x" label="Sidebar width" current={() => nav.current.offsetWidth} onResize={setNavWidth}
          min={200} max={() => Math.min(420, window.innerWidth / 2)} />
        <div className="brand">
          <span className="brand-mark"><Network /></span>
          <span>
            <span className="brand-name">ExpandRG</span>
            <span className="brand-sub">Expand Researching Globally</span>
          </span>
        </div>

        <button className="project-switch" onClick={(e) => setMenu(menu?.kind === "project" ? null : { kind: "project", anchor: e.currentTarget })}
          aria-haspopup="menu" aria-expanded={menu?.kind === "project"}>
          <span className="ps-icon"><FlaskConical /></span>
          <span className="ps-text">
            <span className="ps-label">Project</span>
            <span className="ps-name">{project?.name || "No project yet"}</span>
          </span>
          <ChevronsUpDown className="ps-chev" />
        </button>

        <nav className="nav" aria-label="ExpandRG">
          {groups.map((g) => (
            <div className="nav-group" key={g.group}>
              <div className="nav-label">{g.group}</div>
              {g.items.map((item) => {
                const active = current === item.id;
                const Icon = item.icon;
                const count = counts[item.id];
                return (
                  <div key={item.id}>
                    <button className={`nav-item ${active ? "active" : ""}`} onClick={() => open(item.id)} aria-current={active ? "page" : undefined}>
                      <Icon /><span>{item.label}</span>
                      {count > 0 && <span className={`count ${item.id === "alerts" ? "critical" : ""}`}>{count}</span>}
                    </button>
                    {item.sub && active && (
                      <div className="nav-sub">
                        {item.sub.map(([tab, label]) => (
                          <button key={tab} className={`nav-item ${tabs[item.id] === tab ? "active" : ""}`} onClick={() => openTab(item.id, tab)}>
                            {label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <button className="user-card" onClick={(e) => setMenu(menu?.kind === "person" ? null : { kind: "person", anchor: e.currentTarget })}
            aria-haspopup="menu" aria-expanded={menu?.kind === "person"}>
            <Avatar id={session.member.id} name={session.member.display_name} />
            <span className="ps-text">
              <span className="user-name">{session.member.display_name}</span>
              <span className="user-role">{role === "leader" ? <><Crown />Group leader</> : <><Microscope />Researcher</>}</span>
            </span>
            <ChevronsUpDown className="ps-chev" />
          </button>
        </div>
      </aside>
      {drawer && <div className="drawer-shade" onClick={() => setDrawer(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="icon-btn mobile-only" onClick={() => setDrawer(!drawer)} aria-label="Menu"><PanelLeft /></button>
          <div className="crumbs">
            {project && <><span>{project.name}</span><span className="sep">/</span></>}
            <span className="cur">{currentItem?.label}</span>
          </div>
          <div className="topbar-spacer" />
          <button className="role-chip" onClick={(e) => setMenu(menu?.kind === "person" ? null : { kind: "person", anchor: e.currentTarget })} aria-haspopup="menu">
            <Avatar id={session.member.id} name={session.member.display_name} size={20} />
            <span>{session.member.display_name}</span>
            <ChevronsUpDown />
          </button>
          <button className="icon-btn" onClick={toggleTheme} title={theme === "dark" ? "Light theme" : "Dark theme"}
            aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </button>
        </header>

        <main className={`content ${current === "superbrain" || current === "visualize" ? "wide" : ""}`}>
          <div className="narrow-notice">ExpandRG is built for laptop and desktop screens. Widen the window for the best view.</div>
          {!project && !WITHOUT_PROJECT.includes(current) && <p>You are not a member of any project yet.</p>}
          {project && current === "project" && <ProjectView {...shared} />}
          {project && current === "diagnose" && <DiagnoseView key={`${session.member.id}:${project.id}:${diagnosticSeed?.at || ""}`} {...shared}
            seed={diagnosticSeed?.project_id === project.id ? diagnosticSeed : null} onOpenDrafts={() => open("drafts")} onOpenDiagnostic={openDiagnostic} />}
          {project && current === "drafts" && <DraftsView {...shared} onOpenDiagnostic={openDiagnostic} />}
          {project && current === "superbrain" && (
            <SuperBrainView {...shared} tab={tabs.superbrain} onTab={(tab) => setTabs({ ...tabs, superbrain: tab })} />
          )}
          {project && current === "alerts" && (role === "leader"
            ? <RunsView key={project.id} {...shared} onVisualize={visualize} onDiagnose={diagnose} />
            : <SimulationsView key={`${session.member.id}:${project.id}`} {...shared} onVisualize={visualize} onDiagnose={diagnose}
                tab={tabs.alerts} onTab={(tab) => setTabs({ ...tabs, alerts: tab })} />)}
          {project && current === "visualize" && (
            <VisualizeView key={`${session.member.id}:${project.id}`} {...shared} seed={graphSeed} />
          )}
          {project && current === "experiments" && <ExperimentsView {...shared} onVisualize={visualize} />}
          {current === "projects" && <ProjectsView {...shared} onCreated={projectCreated} />}
          {current === "offboarding" && <OffboardingView {...shared} />}
        </main>
      </div>

      {menu?.kind === "project" && (
        <Menu anchor={menu.anchor} label="Switch project" onClose={() => setMenu(null)}>
          {projects.map((p) => (
            <button key={p.id} role="menuitem" className={`menu-item ${p.id === projectId ? "sel" : ""}`}
              onClick={() => { setProjectId(p.id); setMenu(null); }}>
              <FlaskConical />
              <span className="mi-text"><span className="mi-t">{p.name}</span><span className="mi-sub">{p.id}</span></span>
              {p.id === projectId && <Check />}
            </button>
          ))}
          {role === "leader" && <>
            <div className="menu-sep" />
            <button role="menuitem" className="menu-item" onClick={() => { setMenu(null); open("projects"); }}>
              <Plus /><span className="mi-text"><span className="mi-t">New project…</span></span>
            </button>
          </>}
        </Menu>
      )}
      {menu?.kind === "person" && (
        <PersonMenu anchor={menu.anchor} current={session.member.id} onClose={() => setMenu(null)}
          onSwitch={(id) => { setMenu(null); signIn(id).catch(() => signOut()); }} onSignOut={() => { setMenu(null); signOut(); }} />
      )}
    </div>
  );
}

// Who else can sign in (prototype sign-in): switch to them in one click, or sign out.
function PersonMenu({ anchor, current, onClose, onSwitch, onSignOut }) {
  const [members, setMembers] = useState(null);
  useEffect(() => {
    backend("/demo/members").then(setMembers).catch(() => setMembers([]));
  }, []);
  return (
    <Menu anchor={anchor} label="Switch person" onClose={onClose}>
      {members === null && <p className="menu-note">Loading…</p>}
      {(members || []).map((m) => (
        <button key={m.id} role="menuitem" className={`menu-item ${m.id === current ? "sel" : ""}`} onClick={() => onSwitch(m.id)}>
          <Avatar id={m.id} name={m.display_name} size={22} />
          <span className="mi-text"><span className="mi-t">{m.display_name}</span><span className="mi-sub">{m.role === "leader" ? "Group leader" : "Researcher"}</span></span>
          {m.id === current && <Check />}
        </button>
      ))}
      <div className="menu-sep" />
      <button role="menuitem" className="menu-item" onClick={onSignOut}><LogOut /><span className="mi-text"><span className="mi-t">Sign out</span></span></button>
    </Menu>
  );
}
