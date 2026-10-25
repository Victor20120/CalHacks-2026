import { useEffect, useState } from "react";
import { backend } from "../api.js";
import { ErrorNote } from "../components.jsx";

// Group leader only: the group's projects, and a new one with the researchers who work on it.
export default function ProjectsView({ session, projects, onCreated }) {
  const [researchers, setResearchers] = useState([]);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState({}); // member_id -> true
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    backend("/members", { token: session.token })
      .then((members) => {
        const active = members.filter((m) => m.role === "researcher" && m.status === "active");
        setResearchers(active);
        setPicked(Object.fromEntries(active.map((m) => [m.id, true])));
      })
      .catch((e) => setError(e.message));
  }, []);

  const chosen = researchers.filter((m) => picked[m.id]);

  async function create(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const project = await backend("/projects", {
        method: "POST",
        token: session.token,
        body: { name: name.trim(), member_ids: chosen.map((m) => m.id) },
      });
      setName("");
      setNotice(`${project.name} is ready. Researchers see it after reloading the dashboard; in VS Code, they open its folder and click "ExpandRG: Set up".`);
      await onCreated(project.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view">
      <h2>Projects</h2>
      <p className="muted">Each project has its own updates, worksheets and runs. Only the researchers on a project see it.</p>
      <ErrorNote error={error} />

      <section className="card">
        <h3>The group's projects</h3>
        {projects.length ? (
          <table className="file-table">
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td><strong>{p.name}</strong></td>
                  <td className="muted small">{p.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No projects yet.</p>
        )}
      </section>

      <section className="card">
        <h3>New project</h3>
        <form onSubmit={create}>
          <label>
            Name
            <input value={name} maxLength={100} disabled={busy} placeholder="e.g. Channel flow solver"
              onChange={(e) => setName(e.target.value)} />
          </label>
          <ul className="pick-people" aria-label="Who works on it">
            {researchers.map((m) => (
              <li key={m.id}>
                <label className="check">
                  <input type="checkbox" disabled={busy} checked={!!picked[m.id]}
                    onChange={(e) => setPicked({ ...picked, [m.id]: e.target.checked })} />
                  {m.display_name}
                </label>
              </li>
            ))}
          </ul>
          <p className="muted small">You're on every project. Untick anyone who doesn't work on this one.</p>
          <button className="primary" type="submit" disabled={busy || !name.trim()}>
            {busy ? "Creating…" : "Create project"}
          </button>
        </form>
        {notice && <p className="muted" role="status">{notice}</p>}
      </section>
    </div>
  );
}
