import { useEffect, useState } from "react";
import { CONNECTOR_HELP, connector } from "../api.js";
import { CompareCharts, formatValue } from "../charts.jsx";
import { ErrorNote, FolderPicker } from "../components.jsx";

const MAX_VARIANTS = 3; // the comparison palette is checked for three colours
const ACTIVE = ["queued", "running"];

// Try a change in a copy of the project and compare it with the original run.
// Preparing only makes copies; nothing runs until the researcher clicks Run.
export default function ExperimentsView({ session, project }) {
  const [root, setRoot] = useState("");
  const [manifest, setManifest] = useState(null);
  const [manifestError, setManifestError] = useState("");
  const [experiments, setExperiments] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    setManifest(null);
    setManifestError("");
    if (!root) return;
    connector("/manifest", { method: "POST", body: { root } })
      .then(setManifest)
      .catch((e) => setManifestError(e.message));
  }, [root]);

  async function load() {
    try {
      const query = `?member_id=${encodeURIComponent(session.member.id)}&project_id=${encodeURIComponent(project.id)}`;
      setExperiments(await connector(`/experiments${query}`));
      setError("");
    } catch {
      setError(CONNECTOR_HELP);
    }
  }

  // Refresh every second while something waits or runs, every 5 seconds otherwise.
  const busy = experiments.some((e) => ACTIVE.includes(e.status));
  useEffect(() => {
    load();
    const timer = setInterval(load, busy ? 1000 : 5000);
    return () => clearInterval(timer);
  }, [project.id, busy]);

  async function act(path, body, method = "POST") {
    try {
      await connector(path, { method, body });
      await load();
    } catch (e) {
      setError(e.message);
    }
  }

  const sets = groupBySet(experiments);

  return (
    <div className="view">
      <h2>Experiments</h2>
      <p className="muted">
        Try a change in a copy of your project and compare it with the original run. Preparing only makes copies:
        nothing runs until you click <strong>Run</strong>, and your project files are never changed.
      </p>

      <section className="card">
        <h3>New comparison</h3>
        <FolderPicker root={root} onChange={setRoot} />
        {manifestError && <ManifestHelp message={manifestError} />}
        {manifest && (
          <NewComparison
            root={root}
            manifest={manifest}
            session={session}
            project={project}
            onPrepared={load}
            onError={setError}
          />
        )}
      </section>
      <ErrorNote error={error} />

      {sets.map((set) => (
        <ComparisonCard key={set.id} set={set} onAct={act} />
      ))}
    </div>
  );
}

function ManifestHelp({ message }) {
  const [first, ...rest] = message.split("\n");
  return (
    <div className="notice">
      <p>{first}</p>
      {rest.length > 0 && <pre>{rest.join("\n")}</pre>}
    </div>
  );
}

function NewComparison({ root, manifest, session, project, onPrepared, onError }) {
  const [entryName, setEntryName] = useState(manifest.entry_points[0].name);
  const [baseline, setBaseline] = useState(manifest.logs.includes("run.log") ? "run.log" : manifest.logs[0] || "");
  const entry = manifest.entry_points.find((e) => e.name === entryName) || manifest.entry_points[0];
  const blank = () => Object.fromEntries(entry.parameters.map((p) => [p, String(entry.values[p] ?? "")]));
  const [variants, setVariants] = useState([blank()]);
  const [busy, setBusy] = useState(false);

  useEffect(() => setVariants([blank()]), [entryName, root]);

  function edit(index, key, value) {
    setVariants(variants.map((v, i) => (i === index ? { ...v, [key]: value } : v)));
  }

  // Only the values that differ from the project's config, as the config's own types.
  function changesOf(variant) {
    const changes = {};
    for (const [key, text] of Object.entries(variant)) {
      const current = entry.values[key];
      if (text.trim() === String(current ?? "")) continue;
      changes[key] = typeof current === "number" ? Number(text) : typeof current === "boolean" ? text.trim() === "true" : text;
    }
    return changes;
  }

  async function prepare() {
    setBusy(true);
    const setId = crypto.randomUUID().slice(0, 12);
    try {
      for (const variant of variants) {
        await connector("/experiments", {
          method: "POST",
          body: { root, member_id: session.member.id, project_id: project.id, entry: entry.name,
                  changes: changesOf(variant), set_id: setId, baseline: baseline || null },
        });
      }
      onError("");
      setVariants([blank()]);
      onPrepared();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="new-comparison">
      <div className="folder-row">
        {manifest.entry_points.length > 1 && (
          <label>
            Run
            <select value={entryName} onChange={(e) => setEntryName(e.target.value)}>
              {manifest.entry_points.map((e) => (
                <option key={e.name}>{e.name}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          Compare with
          <select value={baseline} onChange={(e) => setBaseline(e.target.value)}>
            {manifest.logs.map((log) => (
              <option key={log}>{log}</option>
            ))}
            <option value="">Nothing</option>
          </select>
        </label>
      </div>
      <p className="muted small">
        <code>{entry.command.join(" ")}</code> · limits per run: {entry.limits.wall_seconds} s, {entry.limits.memory_mb} MB
        memory, {entry.limits.output_mb} MB output
      </p>
      {entry.parameters.length === 0 && <p className="muted">This entry point has no parameters to change; it runs as is.</p>}
      <table className="numbers-table variants">
        <thead>
          <tr>
            <th>Experiment</th>
            {entry.parameters.map((p) => (
              <th key={p}>{p}</th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {variants.map((variant, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              {entry.parameters.map((p) => (
                <td key={p}>
                  <input
                    className={variant[p] !== String(entry.values[p] ?? "") ? "changed" : ""}
                    value={variant[p]}
                    onChange={(e) => edit(i, p, e.target.value)}
                    aria-label={`${p} for experiment ${i + 1}`}
                  />
                </td>
              ))}
              <td>
                {variants.length > 1 && (
                  <button className="link" onClick={() => setVariants(variants.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        {variants.length < MAX_VARIANTS && (
          <button onClick={() => setVariants([...variants, blank()])}>Add an experiment</button>
        )}
        <button className="primary" onClick={prepare} disabled={busy}>
          {busy ? "Preparing…" : `Prepare ${variants.length === 1 ? "the experiment" : `${variants.length} experiments`}`}
        </button>
      </div>
      <p className="muted small">Values that differ from your project's config are highlighted. Preparing makes copies; it runs nothing.</p>
    </div>
  );
}

function ComparisonCard({ set, onAct }) {
  const [details, setDetails] = useState({});
  const prepared = set.items.filter((e) => e.status === "prepared");
  const budget = prepared.reduce((sum, e) => sum + e.limits.wall_seconds, 0);
  const stamp = set.items.map((e) => `${e.id}:${e.status}:${e.finished_at}`).join("|");

  useEffect(() => {
    let stopped = false;
    Promise.all(set.items.map((e) => connector(`/experiments/${e.id}`)))
      .then((list) => !stopped && setDetails(Object.fromEntries(list.map((d) => [d.id, d]))))
      .catch(() => {});
    return () => {
      stopped = true;
    };
  }, [stamp]);

  const first = details[set.items[0].id];
  const baseline = first?.baseline_result;
  const tones = ["line-1", "line-2", "line-3"];
  const lines = [
    ...(baseline ? [{ key: "baseline", name: `Original (${first.baseline})`, tone: "line-baseline", series: baseline.series, result: baseline }] : []),
    ...set.items.map((e, i) => ({ key: e.id, name: e.label, tone: tones[i % tones.length], series: details[e.id]?.series || {},
                                   result: details[e.id], status: e })),
  ];
  const hasNumbers = lines.some((l) => Object.keys(l.series).length);

  return (
    <section className="card comparison">
      <div className="card-header">
        <h3>
          {set.items[0].entry} <span className="muted small">· prepared {localTime(set.items[set.items.length - 1].created_at)}</span>
        </h3>
        {prepared.length > 1 && (
          <button className="primary" onClick={() => onAct("/experiments/run", { ids: prepared.map((e) => e.id) })}>
            Run all ({prepared.length}, up to {minutes(budget)})
          </button>
        )}
      </div>
      <table className="alert-table experiments-table">
        <tbody>
          {set.items.map((e, i) => (
            <tr key={e.id}>
              <td>
                <span className={`legend-key ${tones[i % tones.length]}`} /> {e.label}
              </td>
              <td>
                <span className={`tag status-${e.status}`}>{e.status}</span>
                {e.reason && <div className="muted small">{e.reason}</div>}
              </td>
              <td className="muted small">up to {minutes(e.limits.wall_seconds)}</td>
              <td className="row-action">
                {e.status === "prepared" && (
                  <button className="primary" onClick={() => onAct("/experiments/run", { ids: [e.id] })}>
                    Run
                  </button>
                )}
                {ACTIVE.includes(e.status) && <button onClick={() => onAct(`/experiments/${e.id}/stop`, {})}>Stop</button>}
                {!ACTIVE.includes(e.status) && (
                  <button className="link" onClick={() => onAct(`/experiments/${e.id}`, undefined, "DELETE")}>
                    Delete
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {hasNumbers && (
        <>
          <CompareCharts lines={lines.filter((l) => Object.keys(l.series).length)} />
          <Summary lines={lines} />
        </>
      )}
    </section>
  );
}

// The comparison as a table: the same numbers as the charts, readable without hovering.
function Summary({ lines }) {
  const metrics = [...new Set(lines.flatMap((l) => Object.keys(l.series)))].slice(0, 2);
  const firstStep = (result, severity) => result?.alerts?.find((a) => a.severity === severity)?.step ?? "–";
  return (
    <table className="numbers-table">
      <thead>
        <tr>
          <th>Run</th>
          <th>Result</th>
          <th>Last step</th>
          {metrics.map((m) => (
            <th key={m}>Final {m}</th>
          ))}
          <th>First Warning</th>
          <th>First Critical</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l) => (
          <tr key={l.key}>
            <td>
              <span className={`legend-key ${l.tone}`} /> {l.name}
            </td>
            <td>
              {l.status ? l.status.status : "original"}
              {l.result?.alerts && ` · ${verdict(l.result.alerts)}`}
            </td>
            <td>{l.result?.last_step ?? "–"}</td>
            {metrics.map((m) => {
              const points = l.series[m] || [];
              return <td key={m}>{points.length ? formatValue(points[points.length - 1][1]) : "–"}</td>;
            })}
            <td>{firstStep(l.result, "warning")}</td>
            <td>{firstStep(l.result, "critical")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// A run can end normally and still have diverged: say what its alerts showed.
function verdict(alerts) {
  if (alerts.some((a) => a.severity === "critical")) return "Critical alerts";
  if (alerts.some((a) => a.severity === "warning")) return "Warnings";
  return "no alerts";
}

function groupBySet(experiments) {
  const sets = new Map();
  for (const e of experiments) {
    if (!sets.has(e.set_id)) sets.set(e.set_id, { id: e.set_id, items: [] });
    sets.get(e.set_id).items.push(e);
  }
  for (const set of sets.values()) set.items.sort((a, b) => a.created_at.localeCompare(b.created_at));
  return [...sets.values()];
}

function minutes(seconds) {
  return seconds < 120 ? `${seconds} s` : `${Math.round(seconds / 60)} min`;
}

function localTime(iso) {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
