import { useEffect, useState } from "react";
import { CATEGORIES, VERIFICATION_STATUSES, backend } from "../api.js";
import { ErrorNote } from "../components.jsx";

export default function DraftsView({ session, projects }) {
  const [drafts, setDrafts] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    try {
      setDrafts(await backend("/drafts", { token: session.token }));
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function decide(draft, action, edits) {
    setError("");
    try {
      if (action === "approve") {
        const finding = await backend(`/drafts/${draft.id}/approve`, { method: "POST", body: edits, token: session.token });
        setMessage(`Shared to the Super Brain: "${finding.title}" (${finding.verification_status}).`);
      } else {
        const result = await backend(`/drafts/${draft.id}/reject`, { method: "POST", token: session.token });
        setMessage(result.detail);
      }
      await load();
    } catch (e) {
      setError(e.message);
    }
  }

  const projectName = (id) => projects.find((p) => p.id === id)?.name || id;

  return (
    <div className="view">
      <h2>Drafts for the Super Brain</h2>
      <p className="muted">
        Only you can see these. Nothing is shared until you approve it; edit it first so it says only what your group needs.
      </p>
      {message && <p className="success-note">{message}</p>}
      <ErrorNote error={error} />
      {drafts && !drafts.length && <p className="muted">No drafts waiting.</p>}
      {drafts?.map((d) => (
        <DraftCard key={d.id} draft={d} projectName={projectName(d.project_id)} onDecide={decide} />
      ))}
    </div>
  );
}

function DraftCard({ draft, projectName, onDecide }) {
  const [title, setTitle] = useState(draft.title);
  const [summary, setSummary] = useState(draft.summary);
  const [category, setCategory] = useState(draft.category);
  const [status, setStatus] = useState(draft.verification_status);

  return (
    <section className="card draft">
      <p className="muted small">
        {projectName} · from CREAO run {draft.source_creao_run_id} · {draft.created_at}
      </p>
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
      </label>
      <label>
        Summary
        <textarea rows={8} value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={4000} />
      </label>
      <div className="options">
        <label>
          Category
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Verification
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {VERIFICATION_STATUSES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      {status === "verified" && (
        <p className="small warning-note">Choose "verified" only if you have checked it yourself, for example by rerunning.</p>
      )}
      <div className="actions">
        <button
          className="primary"
          onClick={() => onDecide(draft, "approve", { title, summary, category, verification_status: status })}
        >
          Share with everyone in {projectName}
        </button>
        <button onClick={() => onDecide(draft, "reject")}>Keep private</button>
      </div>
    </section>
  );
}
