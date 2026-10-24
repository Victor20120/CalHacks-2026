import { useEffect, useState } from "react";
import { backend } from "../api.js";
import { RunCharts, RunNumbers } from "../charts.jsx";
import { ErrorNote, SeverityBadge } from "../components.jsx";

const GRAPHS = 3;
const REPLAY_MS = 8000;

// Runs in the selected project: the list on the left (live ones first), the chosen run on
// the right with its graphs and alerts. A researcher starts watching a log here; the local
// connector on their computer reads it and sends the numbers. The group leader sees every
// researcher's runs.
export default function RunsView({ session, project, initialRunId }) {
  const isLeader = session.member.role === "leader";
  const runs = useRuns(session, project.id);
  const [selectedId, setSelectedId] = useState(initialRunId || null);
  useEffect(() => { if (initialRunId) setSelectedId(initialRunId); }, [initialRunId]);

  const list = runs.list || [];
  const selected = list.find((r) => r.id === selectedId) || list.find((r) => r.watching) || list[0] || null;

  return (
    <div className="view">
      <h2>Monitor</h2>
      <p className="lead">{isLeader ? `Your researchers' simulations in ${project.name}. ` : 'Your simulations and previous results. '}
        New runs connect here automatically. Follow live metrics and problems, or open an earlier run to review and replay its results.
      </p>
      <SeverityGuide />
      <ErrorNote error={runs.error} />

      <div className="runs-layout">
        <nav className="run-list" aria-label="Runs">
          <h4>Live runs</h4>
          {!list.some((r) => r.watching) && <p className="muted small">No simulations are running right now.</p>}
          {list
            .filter((r) => r.watching)
            .map((r) => (
              <RunItem key={r.id} run={r} selected={r.id === selected?.id} onSelect={setSelectedId} showWho={isLeader} />
            ))}
          <h4>Previous runs</h4>
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
          />
        ) : (
          <section className="run-viewer">
            <p className="muted">
              {runs.list === null
                ? "Loading…"
                : isLeader
                  ? "No runs in this project yet. A run appears automatically when a researcher starts a simulation."
                  : "No runs yet. Prepare a version in Set up & run, then click Run simulation."}
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
      {run.critical > 0 || run.warning > 0 ? (
        <span className="alert-counts">
          {run.critical > 0 && <span className="badge severity-critical">{run.critical} critical</span>}
          {run.warning > 0 && <span className="badge severity-warning">{run.warning} warning</span>}
        </span>
      ) : (
        <span className="small muted">No problems found</span>
      )}
    </button>
  );
}

function SeverityGuide() {
  return (
    <div className="severity-guide">
      <span>
        <SeverityBadge severity="warning" /> something looks off: a value suddenly jumped, or the residual keeps rising
      </span>
      <span>
        <SeverityBadge severity="critical" /> the run is failing: NaN or Inf, values too large to be real, or an error line
      </span>
    </div>
  );
}

// One run: live while it's watched, or replayed step by step afterwards.
function RunViewer({ session, run }) {
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

  const shown = detail && replayStep !== null ? upTo(detail, replayStep) : detail;
  const lastStep = run.last_step ?? "–";

  return (
    <section className="run-viewer">
      <div className="run-head">
        <div>
          <h3>
            <code>{run.run_label}</code>{" "}
            {run.watching ? <span className="tag live">Live</span> : <span className="tag">Finished</span>}
          </h3>
          <p className="muted small">
            {run.member_name}'s simulation · started {stamp(run.started_at)} ·{" "}
            {replayStep !== null ? `replaying, step ${replayStep} of ${lastStep}` : `reached step ${lastStep}`} · last
            update {stamp(run.updated_at).slice(11)}
          </p>
        </div>
        <div className="run-actions">
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
            ? "Waiting for the first simulation metrics. Build output appears in the saved version while compilation runs…"
            : "This run did not emit numeric step metrics. Its output and setup are kept with the saved version."}
        </p>
      )}
      {detail && detail.metrics.length > 0 && (
        <p className="muted small">
          Each graph is one number the simulation writes at every step. A vertical line marks the step where a problem was
          found.
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
          <h4>All problems found</h4>
          {!shown.alerts.length && <p className="muted small">No problems found{replayStep !== null ? " yet" : ""}.</p>}
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
  if (!latest.length) return <p className="no-problems">No problems found so far.</p>;
  return (
    <>
      <p className="strip-title">Latest problems found</p>
      <ul className="latest-alerts" aria-label="Latest problems found">
        {latest.map((a) => (
          <li key={a.id} className={`severity-border-${a.severity}`}>
            <SeverityBadge severity={a.severity} />
            {a.step !== null && <span className="num">step {a.step}</span>}
            <span>{a.message}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

// The run with only what had happened by `step`, for a replay.
function upTo(detail, step) {
  const series = Object.fromEntries(Object.entries(detail.series).map(([m, points]) => [m, points.filter(([s]) => s <= step)]));
  return { ...detail, series, alerts: detail.alerts.filter((a) => a.step === null || a.step <= step) };
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

// Local date and time, e.g. 2026-10-24 14:02:11.
function stamp(iso) {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
