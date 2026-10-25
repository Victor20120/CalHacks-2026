import { useEffect, useRef, useState } from "react";
import { DEPTHS, FOCUS_AREAS, backend, connector } from "../api.js";
import { DiagnosticGraphs } from "./VisualizeView.jsx";
import { AgentStatusBadge, ErrorNote, FolderPicker, Markdown, VerificationBadge } from "../components.jsx";

export default function DiagnoseView({ session, project, onOpenDrafts, seed, onOpenDiagnostic }) {
  const [root, setRoot] = useState("");
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState({}); // path -> line range ("" = whole file / log excerpt)
  const [symptom, setSymptom] = useState(seed?.symptom || "");
  const [focus, setFocus] = useState(["numerical"]);
  const [depth, setDepth] = useState("standard");
  const [useSuperBrain, setUseSuperBrain] = useState(true);
  const [preview, setPreview] = useState(null);
  const [job, setJob] = useState(null);
  const [started, setStarted] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const poller = useRef(null);
  const fromAlert = Boolean(seed?.run_id && seed?.alert_id);
  const folderName = root.split(/[\\/]/).filter(Boolean).pop() || ""; // the heading names the folder, not the group's project

  useEffect(() => {
    setFiles([]);
    setSelected({});
    setPreview(null);
    if (!root) return;
    connector("/files", { method: "POST", body: { root } })
      .then((r) => setFiles(r.files))
      .catch((e) => setError(e.message));
  }, [root]);

  useEffect(() => () => clearInterval(poller.current), []);

  const selections = Object.entries(selected).map(([path, range]) => (range.trim() ? `${path}:${range.trim()}` : path));

  function toggleFile(path) {
    setPreview(null);
    setSelected((current) => {
      const next = { ...current };
      if (path in next) delete next[path];
      else next[path] = "";
      return next;
    });
  }

  async function makePreview() {
    setError("");
    try {
      setPreview(await connector("/evidence", { method: "POST", body: { root, selections } }));
    } catch (e) {
      setError(e.message);
    }
  }

  async function send() {
    setBusy(true);
    setError("");
    setJob(null);
    try {
      const body = {
        project_id: project.id,
        symptom,
        focus_areas: focus,
        analysis_depth: depth,
        evidence: preview?.evidence || [],
        ...(fromAlert ? { run_id: seed.run_id, alert_id: seed.alert_id } : {}),
        use_super_brain: useSuperBrain,
      };
      const start = await backend("/diagnose", { method: "POST", body, token: session.token });
      setStarted(start);
      clearInterval(poller.current);
      poller.current = setInterval(async () => {
        try {
          const latest = await backend(`/diagnose/${start.job_id}`, { token: session.token });
          if (latest.status !== "running") {
            clearInterval(poller.current);
            setJob(latest);
            setBusy(false);
          }
        } catch (e) {
          clearInterval(poller.current);
          setError(e.message);
          setBusy(false);
        }
      }, 750); // the backend is on this computer: checking often costs nothing
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <div className="view">
      <h2>Diagnose a problem in {folderName || project.name}</h2>
      <p className="muted">
        {fromAlert
          ? "Your own agent analyzes the selected alert and its run. Review the problem below and add file excerpts if needed."
          : "Your own agent analyzes only the evidence you select below. You see exactly what is sent before anything leaves this computer."}
      </p>

      <section className="card">
        <h3>1. Evidence</h3>
        {fromAlert && <div className="notice"><strong>Selected run problem</strong><p>{seed.symptom}</p>
          <p className="muted small">Your agent will receive this alert and a summary of its run. You can add file excerpts below. Nothing is sent until you choose Send to my agent.</p></div>}
        <FolderPicker root={root} onChange={setRoot} />
        {files.length > 0 && (
          <>
          <p className="muted small file-count">
            {files.length} files · {Object.keys(selected).length} selected
          </p>
          <div className="file-scroll">
          <table className="file-table">
            <thead>
              <tr>
                <th />
                <th>File</th>
                <th>Kind</th>
                <th>Lines (optional, e.g. 18-25)</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.path}>
                  <td>
                    <input type="checkbox" checked={f.path in selected} onChange={() => toggleFile(f.path)} />
                  </td>
                  <td>
                    <code>{f.path}</code>
                  </td>
                  <td className="muted">{f.kind}</td>
                  <td>
                    {f.path in selected && f.kind !== "log" && (
                      <input
                        className="range"
                        value={selected[f.path]}
                        placeholder="whole file"
                        onChange={(e) => {
                          setPreview(null);
                          setSelected({ ...selected, [f.path]: e.target.value });
                        }}
                      />
                    )}
                    {f.path in selected && f.kind === "log" && <span className="muted small">start, anomalies and end</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {Object.keys(selected).length > 0 && (
            <p className="small file-picked">
              Selected: {Object.entries(selected).map(([path, lines]) => (lines ? `${path} (${lines})` : path)).join(", ")}
            </p>
          )}
          </>
        )}
      </section>

      <section className="card">
        <h3>2. What went wrong</h3>
        <textarea
          rows={3}
          value={symptom}
          onChange={(e) => setSymptom(e.target.value)}
          placeholder="e.g. max|T| reaches inf at step 2223; expected the temperature to decay toward 0"
        />
        <div className="options">
          <fieldset>
            <legend>Focus</legend>
            {FOCUS_AREAS.map((area) => (
              <label key={area} className="check">
                <input
                  type="checkbox"
                  checked={focus.includes(area)}
                  onChange={() => setFocus(focus.includes(area) ? focus.filter((a) => a !== area) : [...focus, area])}
                />
                {area}
              </label>
            ))}
          </fieldset>
          <label>
            Depth
            <select value={depth} onChange={(e) => setDepth(e.target.value)}>
              {DEPTHS.map((d) => (
                <option key={d}>{d}</option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={useSuperBrain} onChange={(e) => setUseSuperBrain(e.target.checked)} />
            Give my agent relevant findings from the Super Brain
          </label>
        </div>
      </section>

      <section className="card">
        <h3>3. Check and send</h3>
        <button onClick={makePreview} disabled={!root || !selections.length}>
          Preview what will be sent
        </button>
        {preview && (
          <div className="preview">
            <p>
              {preview.evidence.length} excerpt(s), {preview.total_chars} characters
              {preview.redactions > 0 && ` · ${preview.redactions} secret(s) blanked out`}
              {preview.skipped.length > 0 && ` · left out (size limit): ${preview.skipped.join(", ")}`}
            </p>
            {preview.evidence.map((e) => (
              <details key={e.path}>
                <summary>
                  <code>{e.path}</code> ({e.kind}
                  {e.truncated ? ", partial" : ""}, {e.excerpt.length} characters)
                </summary>
                <pre>{e.excerpt}</pre>
              </details>
            ))}
          </div>
        )}
        {(preview || fromAlert) && <div className="actions"><button className="primary" onClick={send} disabled={busy || !symptom.trim() || !focus.length}>
          {busy ? "Your agent is working…" : "Send to my agent"}
        </button></div>}
        <ErrorNote error={error} />
      </section>

      {started && (
        <section className="card">
          <h3>Result</h3>
          {started.prior_findings.length > 0 && (
            <div className="prior">
              <h4>Retrieved from the Super Brain</h4>
              {started.prior_findings.map((f) => (
                <p key={f.id}>
                  <VerificationBadge status={f.verification_status} /> {f.title}{" "}
                  <span className="muted small">shared by {f.contributed_by_name}</span>
                </p>
              ))}
            </div>
          )}
          <DiagnosticGraphs graphs={job?.diagnosis?.diagnostic_graphs || started.diagnostic_graphs} session={session} onOpen={onOpenDiagnostic} />
          {!job && <p className="muted">Waiting for your agent (usually about a minute)…</p>}
          {job?.status === "failed" && <ErrorNote error={`Diagnosis failed (${job.error.type}): ${job.error.message}`} />}
          {job?.status === "completed" && <Diagnosis job={job} onOpenDrafts={onOpenDrafts} />}
        </section>
      )}
    </div>
  );
}

function Diagnosis({ job, onOpenDrafts }) {
  const { diagnosis } = job;
  return (
    <>
      {diagnosis.issues.length > 0 && (
        <>
          <h4>Issues found (the agent's own labels, not verified by a researcher)</h4>
          <ol className="issues">
            {diagnosis.issues.map((issue, i) => (
              <li key={i}>
                <p>
                  <AgentStatusBadge status={issue.status} confidence={issue.confidence} /> <strong>{issue.title}</strong>
                </p>
                <p className="small">
                  <span className="label">Evidence</span> {issue.evidence}
                </p>
                <p className="small">
                  <span className="label">How to check</span> {issue.proposed_validation}
                </p>
              </li>
            ))}
          </ol>
        </>
      )}
      <h4>Your agent's explanation</h4>
      <Markdown text={diagnosis.reply} />
      <p className="muted small">CREAO run {diagnosis.run_id} · saved to your private memory</p>
      {job.warning && <ErrorNote error={job.warning} />}
      {job.draft_id && (
        <div className="draft-note">
          A draft finding is ready. It stays private unless you approve it.{" "}
          <button onClick={onOpenDrafts}>Review the draft</button>
        </div>
      )}
    </>
  );
}
