import { useEffect, useState } from "react";
import { CONNECTOR_HELP, backend, connector } from "../api.js";
import { RunCharts, RunNumbers } from "../charts.jsx";
import { ErrorNote, SeverityBadge } from "../components.jsx";

const GRAPHS = 3;
const REPLAY_MS = 8000;

// Runs in the selected project: the list on the left (live ones first), the chosen run on
// the right with its graphs and alerts. A researcher starts watching a log here; the local
// connector on their computer reads it and sends the numbers. The group leader sees every
// researcher's runs.
export default function RunsView({ session, project }) {
  const isLeader = session.member.role === "leader";
  const runs = useRuns(session, project.id);
  const watches = useWatches(session, !isLeader);
  const [selectedId, setSelectedId] = useState(null);

  const list = runs.list || [];
  const selected = list.find((r) => r.id === selectedId) || list.find((r) => r.watching) || list[0] || null;

  return (
    <div className="view">
      <h2>Runs</h2>
      <p className="muted">
        {isLeader
          ? `Your researchers' simulation runs in ${project.name}: live while they are being watched, and every earlier run.`
          : "Watch a simulation's log: it shows here live with its alerts, and earlier runs stay here to open again. Your group leader sees your runs too."}{" "}
        ExpandRG only reads the log. It never stops a simulation.
      </p>
      {!isLeader && <WatchBar session={session} project={project} onStarted={setSelectedId} />}
      <ErrorNote error={runs.error} />

      <div className="runs-layout">
        <nav className="run-list" aria-label="Runs">
          <h4>Live</h4>
          {!list.some((r) => r.watching) && <p className="muted small">Nothing is being watched right now.</p>}
          {list
            .filter((r) => r.watching)
            .map((r) => (
              <RunItem key={r.id} run={r} selected={r.id === selected?.id} onSelect={setSelectedId} showWho={isLeader} />
            ))}
          <h4>Earlier runs</h4>
          {!list.some((r) => !r.watching) && <p className="muted small">None yet.</p>}
          {list
            .filter((r) => !r.watching)
            .map((r) => (
              <RunItem key={r.id} run={r} selected={r.id === selected?.id} onSelect={setSelectedId} showWho={isLeader} />
            ))}
        </nav>

        {selected ? (
          <RunViewer
            key={selected.id}
            session={session}
            run={selected}
            watch={watches.find((w) => w.run_id === selected.id)}
          />
        ) : (
          <section className="run-viewer">
            <p className="muted">
              {runs.list === null
                ? "Loading…"
                : isLeader
                  ? "No runs in this project yet."
                  : "No runs yet. Pick a log above and click Start watching."}
            </p>
          </section>
        )}
      </div>
    </div>
  );
}

function RunItem({ run, selected, onSelect, showWho }) {
  return (
    <button className={`run-item ${selected ? "selected" : ""}`} onClick={() => onSelect(run.id)}>
      <code>{run.run_label}</code>
      <span className="small muted">
        {showWho && `${run.member_name} · `}
        {stamp(run.started_at)}
      </span>
      {(run.critical > 0 || run.warning > 0) && (
        <span className="alert-counts">
          {run.critical > 0 && <span className="badge severity-critical">{run.critical} critical</span>}
          {run.warning > 0 && <span className="badge severity-warning">{run.warning} warning</span>}
        </span>
      )}
    </button>
  );
}

// One run: live while it's watched, or replayed step by step afterwards.
function RunViewer({ session, run, watch }) {
  const [detail, setDetail] = useState(null);
  const [replayStep, setReplayStep] = useState(null); // null: show the whole run
  const [error, setError] = useState("");

  // The run list refreshes every few seconds; the graphs refresh whenever it says the run changed.
  useEffect(() => {
    backend(`/simulation-runs/${run.id}`, { token: session.token })
      .then((d) => {
        setDetail(d);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [run.id, run.updated_at]);

  const steps = detail ? [...new Set(Object.values(detail.series).flatMap((s) => s.map(([step]) => step)))].sort((a, b) => a - b) : [];

  useEffect(() => {
    if (replayStep === null) return undefined;
    const tick = Math.max(40, REPLAY_MS / Math.max(steps.length, 1));
    const timer = setTimeout(() => {
      const next = steps.find((s) => s > replayStep);
      setReplayStep(next === undefined ? null : next);
    }, tick);
    return () => clearTimeout(timer);
  }, [replayStep]);

  async function stopWatching() {
    try {
      await connector(`/watches/${watch.id}`, { method: "DELETE" });
    } catch (e) {
      setError(e.message);
    }
  }

  const shown = detail && replayStep !== null ? upTo(detail, replayStep) : detail;
  const lastStep = run.last_step ?? "–";

  return (
    <section className="run-viewer">
      <div className="run-head">
        <div>
          <h3>
            <code>{run.run_label}</code>{" "}
            {run.watching ? <span className="tag live">Live</span> : <span className="tag">Stopped</span>}
          </h3>
          <p className="muted small">
            {run.member_name} · started {stamp(run.started_at)} ·{" "}
            {replayStep !== null ? `replaying, step ${replayStep} of ${lastStep}` : `step ${lastStep}`} · last update{" "}
            {stamp(run.updated_at).slice(11)}
          </p>
        </div>
        <div className="run-actions">
          {watch && <button onClick={stopWatching}>Stop watching</button>}
          {!run.watching && detail && steps.length > 0 && (
            <button onClick={() => setReplayStep(replayStep === null ? steps[0] : null)}>
              {replayStep === null ? "Replay" : "Stop replay"}
            </button>
          )}
        </div>
      </div>
      <ErrorNote error={error} />
      {shown && <LatestAlerts alerts={shown.alerts} />}
      {!detail && !error && <p className="muted">Loading…</p>}
      {detail && !detail.metrics.length && (
        <p className="muted">
          {run.watching
            ? "Waiting for the first step numbers from the log…"
            : "This log had no step numbers to plot (lines like: step 200  max|T| = 2.75e+14)."}
        </p>
      )}
      {detail && detail.metrics.length > 0 && <RunCharts run={shown} full={detail} limit={GRAPHS} />}
      {detail && detail.metrics.length > GRAPHS && (
        <p className="muted small">
          Showing the first {GRAPHS} of {detail.metrics.length} measures: {detail.metrics.join(", ")}.
        </p>
      )}

      {shown && (
        <>
          <h4>All alerts</h4>
          {!shown.alerts.length && <p className="muted small">No alerts{replayStep !== null ? " yet" : ""}.</p>}
          <table className="alert-table">
            <tbody>
              {[...shown.alerts].reverse().map((a) => (
                <tr key={a.id}>
                  <td>
                    <SeverityBadge severity={a.severity} />
                  </td>
                  <td className="num">{a.step !== null && `step ${a.step}`}</td>
                  <td>{a.message}</td>
                  <td className="muted small">{stamp(a.created_at).slice(11)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {detail && detail.metrics.length > 0 && (
        <details>
          <summary>Numbers as a table</summary>
          <RunNumbers run={detail} limit={GRAPHS} />
        </details>
      )}
    </section>
  );
}

// The newest Warning and Critical alerts, right under the run's header, so they're seen without scrolling.
function LatestAlerts({ alerts }) {
  const latest = alerts.filter((a) => a.severity !== "informational").slice(-2).reverse();
  if (!latest.length) return null;
  return (
    <ul className="latest-alerts" aria-label="Latest alerts">
      {latest.map((a) => (
        <li key={a.id} className={`severity-border-${a.severity}`}>
          <SeverityBadge severity={a.severity} />
          {a.step !== null && <span className="num">step {a.step}</span>}
          <span>{a.message}</span>
        </li>
      ))}
    </ul>
  );
}

// Pick a log from the folders granted to the local connector and start watching it.
function WatchBar({ session, project, onStarted }) {
  const [logs, setLogs] = useState([]);
  const [choice, setChoice] = useState("");
  const [adding, setAdding] = useState(false);
  const [folder, setFolder] = useState("");
  const [error, setError] = useState("");

  async function loadLogs() {
    try {
      const grants = await connector("/grants");
      const lists = await Promise.all(
        grants.map((g) =>
          connector("/files", { method: "POST", body: { root: g.root } })
            .then((r) => r.files.filter((f) => f.kind === "log").map((f) => ({ root: g.root, folder: g.name, path: f.path })))
            .catch(() => []),
        ),
      );
      const all = lists.flat();
      setLogs(all);
      setChoice((current) => (all.some((l) => keyOf(l) === current) ? current : all[0] ? keyOf(all[0]) : ""));
      setError("");
    } catch {
      setError(CONNECTOR_HELP);
    }
  }

  // New log files (a run that just started) show up without reloading the page.
  useEffect(() => {
    loadLogs();
    const timer = setInterval(loadLogs, 5000);
    return () => clearInterval(timer);
  }, []);

  async function start() {
    const log = logs.find((l) => keyOf(l) === choice);
    try {
      const body = { root: log.root, file: log.path, project_id: project.id, member_id: session.member.id };
      const started = await connector("/watches", { method: "POST", body });
      setError("");
      onStarted(started.run_id);
    } catch (e) {
      setError(e.message);
    }
  }

  async function grant(event) {
    event.preventDefault();
    try {
      await connector("/grants", { method: "POST", body: { root: folder } });
      setFolder("");
      setAdding(false);
      await loadLogs();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section className="watch-bar">
      <div className="watch-row">
        <span className="label">Watch a run</span>
        <select value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Log file">
          {!logs.length && <option value="">No log files in your folders yet</option>}
          {logs.map((l) => (
            <option key={keyOf(l)} value={keyOf(l)}>
              {l.folder} / {l.path}
            </option>
          ))}
        </select>
        <button className="primary" onClick={start} disabled={!choice}>
          Start watching
        </button>
        <button className="link" onClick={() => setAdding(!adding)}>
          {adding ? "Cancel" : "Add a folder"}
        </button>
      </div>
      {adding && (
        <form className="inline-form" onSubmit={grant}>
          <input
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            placeholder={"The folder your simulation writes its log to, e.g. C:\\research\\heat_solver"}
          />
          <button type="submit" disabled={!folder.trim()}>
            Grant access
          </button>
        </form>
      )}
      <ErrorNote error={error} />
    </section>
  );
}

// The run with only what had happened by `step`, for a replay.
function upTo(detail, step) {
  const series = Object.fromEntries(Object.entries(detail.series).map(([m, points]) => [m, points.filter(([s]) => s <= step)]));
  return { ...detail, series, alerts: detail.alerts.filter((a) => a.step === null || a.step <= step) };
}

function keyOf(log) {
  return `${log.root}|${log.path}`;
}

// Runs in one project, refreshed every 2 seconds.
function useRuns(session, projectId) {
  const [state, setState] = useState({ list: null, error: "" });
  useEffect(() => {
    let stopped = false;
    setState({ list: null, error: "" });
    async function load() {
      try {
        const list = await backend(`/simulation-runs?project_id=${encodeURIComponent(projectId)}`, { token: session.token });
        if (!stopped) setState({ list, error: "" });
      } catch (e) {
        if (!stopped) setState((s) => ({ ...s, error: e.message }));
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [projectId]);
  return state;
}

// The watches running on this computer for this researcher (so the viewer can offer Stop watching).
function useWatches(session, enabled) {
  const [watches, setWatches] = useState([]);
  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    async function load() {
      try {
        const list = await connector("/watches");
        if (!stopped) setWatches(list.filter((w) => w.member_id === session.member.id));
      } catch {
        if (!stopped) setWatches([]);
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [enabled]);
  return watches;
}

// Local date and time, e.g. 2026-10-24 14:02:11.
function stamp(iso) {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
