import { useEffect, useRef, useState } from "react";
import { backend, connector } from "../api.js";
import { ErrorNote, FolderPicker, Markdown } from "../components.jsx";

// "What is this project?": your agent's overview, built from a map of the folder you approved. Private to you.
export default function ProjectView({ session, project }) {
  const [saved, setSaved] = useState(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      setSaved(await backend(`/projects/${project.id}/overview`, { token: session.token }));
    } catch (e) {
      setSaved(null);
      if (e.status !== 404) setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setBuilding(false);
    load();
  }, [project.id]);

  return (
    <div className="view">
      <div className="card-header plain">
        <h2>{project.name}</h2>
        {!building && (
          <button className="primary" onClick={() => setBuilding(true)}>
            {saved ? "Refresh overview" : "Build overview"}
          </button>
        )}
      </div>
      <p className="muted">
        Your agent's overview of this project: what it does, how it's organized and how to run it. Only you can see it,
        and your later diagnoses on this project include it automatically.
      </p>
      <ErrorNote error={error} />
      {building && (
        <BuildOverview
          session={session}
          project={project}
          onDone={() => {
            setBuilding(false);
            load();
          }}
          onCancel={() => setBuilding(false)}
        />
      )}
      {!building && !loading && !saved && (
        <p className="notice">No overview yet. Build one so your agent understands the whole project.</p>
      )}
      {!building && saved && <Overview context={saved} />}
    </div>
  );
}

function Overview({ context }) {
  const overview = context.overview;
  return (
    <>
      {overview ? (
        <>
          <section className="card">
            <h3>Purpose</h3>
            <p>{overview.purpose || "Not stated."}</p>
          </section>
          {overview.structure.length > 0 && (
            <section className="card">
              <h3>Structure</h3>
              <table className="file-table">
                <tbody>
                  {overview.structure.map((s) => (
                    <tr key={s.path} className={s.in_project ? "" : "not-found"}>
                      <td>
                        <code>{s.path}</code>
                      </td>
                      <td>
                        {s.role}
                        {!s.in_project && <span className="muted small"> · not found in the project</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {overview.how_to_run && (
            <section className="card">
              <h3>How to run</h3>
              <pre className="wrap">{overview.how_to_run}</pre>
            </section>
          )}
          {overview.key_parameters.length > 0 && (
            <section className="card">
              <h3>Key parameters</h3>
              <table className="file-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Value</th>
                    <th>Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.key_parameters.map((p) => (
                    <tr key={p.name}>
                      <td>
                        <code>{p.name}</code>
                      </td>
                      <td>{p.value}</td>
                      <td>{p.meaning}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {overview.open_questions.length > 0 && (
            <section className="card">
              <h3>Open questions</h3>
              <ul>
                {overview.open_questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      ) : (
        <p className="notice">Your agent didn't return a structured overview this time; its full answer is below.</p>
      )}
      <section className="card">
        <details open={!overview}>
          <summary>Your agent's full answer</summary>
          <Markdown text={context.reply} />
        </details>
        <p className="muted small">
          Built from a map of {context.map_stats.files} files · updated {context.updated_at.replace("T", " ").slice(0, 16)}{" "}
          · CREAO run {context.creao_run_id}
        </p>
      </section>
    </>
  );
}

function BuildOverview({ session, project, onDone, onCancel }) {
  const [root, setRoot] = useState("");
  const [map, setMap] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const poller = useRef(null);

  useEffect(() => () => clearInterval(poller.current), []);

  async function makeMap() {
    setError("");
    setMap(null);
    try {
      setMap(await connector("/project-map", { method: "POST", body: { root } }));
    } catch (e) {
      setError(e.message);
    }
  }

  async function send() {
    setBusy(true);
    setError("");
    try {
      const body = { project_id: project.id, tree: map.tree, files: map.files, excerpts: map.excerpts, stats: map.stats };
      const start = await backend("/overview", { method: "POST", body, token: session.token });
      poller.current = setInterval(async () => {
        try {
          const job = await backend(`/jobs/${start.job_id}`, { token: session.token });
          if (job.status === "running") return;
          clearInterval(poller.current);
          setBusy(false);
          if (job.status === "failed") setError(`Overview failed (${job.error.type}): ${job.error.message}`);
          else onDone();
        } catch (e) {
          clearInterval(poller.current);
          setBusy(false);
          setError(e.message);
        }
      }, 3000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  const neverIncluded = map ? Object.entries(map.stats.left_out_by_rule) : [];

  return (
    <section className="card">
      <h3>Build the overview</h3>
      <FolderPicker root={root} onChange={(r) => (setRoot(r), setMap(null))} />
      <div className="actions">
        <button onClick={makeMap} disabled={!root || busy}>
          Map this folder
        </button>
        <button onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
      {map && (
        <div className="preview">
          <p>
            {map.stats.files} files mapped · {map.total_chars} characters will be sent
            {map.redactions > 0 && ` · ${map.redactions} secret(s) blanked out`}
          </p>
          {neverIncluded.length > 0 && (
            <p className="muted small">
              Never included: {neverIncluded.map(([why, n]) => `${n} ${why}`).join(", ")}
            </p>
          )}
          <details>
            <summary>Folder tree</summary>
            <pre>{map.tree}</pre>
          </details>
          <p className="small">Key files (only their start is sent):</p>
          {map.excerpts.map((e) => (
            <details key={e.path}>
              <summary>
                <code>{e.path}</code> ({e.kind}, {e.excerpt.length} characters)
              </summary>
              <pre>{e.excerpt}</pre>
            </details>
          ))}
          <button className="primary" onClick={send} disabled={busy}>
            {busy ? "Your agent is reading the project…" : "Send to my agent"}
          </button>
        </div>
      )}
      <ErrorNote error={error} />
    </section>
  );
}
