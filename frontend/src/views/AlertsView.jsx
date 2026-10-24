import { useEffect, useState } from "react";
import { CONNECTOR_HELP, backend, connector } from "../api.js";
import { ErrorNote, FolderPicker, SeverityBadge } from "../components.jsx";

export default function AlertsView({ session, project, alerts, onCleared }) {
  const [root, setRoot] = useState("");
  const [files, setFiles] = useState([]);
  const [file, setFile] = useState("");
  const [watches, setWatches] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    setFiles([]);
    if (!root) return;
    connector("/files", { method: "POST", body: { root } })
      .then((r) => {
        setFiles(r.files);
        setFile(r.files.find((f) => f.kind === "log")?.path || "");
      })
      .catch((e) => setError(e.message));
  }, [root]);

  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        const list = await connector("/watches");
        if (!stopped) setWatches(list);
      } catch {
        if (!stopped) setError(CONNECTOR_HELP);
      }
    }
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  async function startWatching() {
    setError("");
    try {
      const body = { root, file, project_id: project.id, member_id: session.member.id };
      const started = await connector("/watches", { method: "POST", body });
      setWatches([...watches, started]);
    } catch (e) {
      setError(e.message);
    }
  }

  async function stopWatching(id) {
    try {
      await connector(`/watches/${id}`, { method: "DELETE" });
      setWatches(watches.filter((w) => w.id !== id));
    } catch (e) {
      setError(e.message);
    }
  }

  async function clear() {
    await backend("/alerts", { method: "DELETE", token: session.token });
    onCleared();
  }

  const mine = alerts.filter((a) => a.project_id === project.id);

  return (
    <div className="view">
      <h2>Simulation alerts</h2>
      <p className="notice">
        SimGuard only reads your run's log. It never pauses, stops or kills a simulation, even on a Critical alert.
      </p>

      <section className="card">
        <h3>Watch a run</h3>
        <FolderPicker root={root} onChange={setRoot} />
        {files.length > 0 && (
          <div className="inline-form">
            <select value={file} onChange={(e) => setFile(e.target.value)}>
              {files.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path}
                </option>
              ))}
            </select>
            <button className="primary" onClick={startWatching} disabled={!file}>
              Start watching
            </button>
          </div>
        )}
        {watches.length > 0 && (
          <table className="file-table">
            <thead>
              <tr>
                <th>Log</th>
                <th>State</th>
                <th>Lines read</th>
                <th>Alerts</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {watches.map((w) => (
                <tr key={w.id}>
                  <td>
                    <code>{w.file}</code> <span className="muted small">{w.project_id}</span>
                  </td>
                  <td>{w.state}</td>
                  <td>{w.lines_read}</td>
                  <td>
                    {w.alerts}
                    {w.critical > 0 && <span className="count critical">{w.critical} critical</span>}
                    {w.undelivered > 0 && <span className="muted small"> ({w.undelivered} not delivered)</span>}
                  </td>
                  <td>
                    <button onClick={() => stopWatching(w.id)}>Stop watching</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <ErrorNote error={error} />
      </section>

      <section className="card">
        <div className="card-header">
          <h3>Alerts in {project.name}</h3>
          {alerts.length > 0 && <button onClick={clear}>Clear</button>}
        </div>
        <p className="muted small">Kept for this session only and never saved.</p>
        {!mine.length && <p className="muted">No alerts.</p>}
        <table className="alert-table">
          <tbody>
            {mine.map((a) => (
              <tr key={a.id} className={`severity-row-${a.severity}`}>
                <td>
                  <SeverityBadge severity={a.severity} />
                </td>
                <td>
                  <p>{a.message}</p>
                  {a.line && <code className="small">{a.line}</code>}
                </td>
                <td className="muted small">
                  {a.run_label}
                  <br />
                  {a.created_at.slice(11, 19)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
