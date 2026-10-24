import { useEffect, useState } from "react";
import { backend } from "../api.js";
import { ErrorNote, Markdown, VerificationBadge } from "../components.jsx";

export default function SuperBrainView({ session, project, query, onQuery }) {
  const [results, setResults] = useState(null);
  const [findings, setFindings] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    backend(`/projects/${project.id}/findings`, { token: session.token })
      .then(setFindings)
      .catch((e) => setError(e.message));
  }, [project.id]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults(null);
      return;
    }
    backend(`/superbrain/search?q=${encodeURIComponent(query)}`, { token: session.token })
      .then(setResults)
      .catch((e) => setError(e.message));
  }, [query]);

  if (openId) return <FindingDetail session={session} id={openId} onOpen={setOpenId} onBack={() => setOpenId(null)} />;

  return (
    <div className="view">
      <h2>Super Brain</h2>
      <p className="muted">Approved findings from your research group. You only see projects you belong to.</p>
      <form className="inline-form" onSubmit={(e) => e.preventDefault()}>
        <input value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Ask Super Brain…" />
      </form>
      <ErrorNote error={error} />
      {results && (
        <section className="card">
          <h3>Matches for "{query}"</h3>
          {!results.length && <p className="muted">No matching findings in your projects.</p>}
          <FindingList findings={results} onOpen={setOpenId} showScore />
        </section>
      )}
      <section className="card">
        <h3>All findings in {project.name}</h3>
        {!findings.length && <p className="muted">No shared findings in this project yet.</p>}
        <FindingList findings={findings} onOpen={setOpenId} />
      </section>
    </div>
  );
}

// From an offboarding handoff: shared by group policy, not approved by its author.
function HandedOffBadge() {
  return <span className="badge handed-off">handed off at offboarding</span>;
}

function FindingList({ findings, onOpen, showScore }) {
  return (
    <ul className="finding-list">
      {findings.map((f) => (
        <li key={f.id}>
          <button className="link" onClick={() => onOpen(f.id)}>
            {f.title}
          </button>
          <div className="small muted">
            <VerificationBadge status={f.verification_status} /> {f.handed_off && <HandedOffBadge />} {f.project_id} ·{" "}
            {f.handed_off ? "from" : "shared by"} {f.contributed_by_name} on {f.approved_at.slice(0, 10)}
            {showScore && ` · match score ${f.score}`}
          </div>
        </li>
      ))}
    </ul>
  );
}

function FindingDetail({ session, id, onOpen, onBack }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setDetail(null);
    backend(`/findings/${id}`, { token: session.token })
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [id]);

  return (
    <div className="view">
      <button className="link" onClick={onBack}>
        ‹ Back to the Super Brain
      </button>
      <ErrorNote error={error} />
      {detail && (
        <section className="card">
          <h2>{detail.finding.title}</h2>
          <p>
            <VerificationBadge status={detail.finding.verification_status} />{" "}
            {detail.finding.handed_off && <HandedOffBadge />}{" "}
            <span className="muted small">
              {detail.finding.category} · {detail.finding.project_id} · shared by {detail.finding.contributed_by_name} on{" "}
              {detail.finding.approved_at}
              {detail.finding.edited_before_approval && " · edited before approval"} · CREAO run{" "}
              {detail.finding.source_creao_run_id}
            </span>
          </p>
          <Markdown text={detail.finding.summary} />
          <h3>Linked findings</h3>
          {!detail.related.length && <p className="muted">None that you can see.</p>}
          <ul className="finding-list">
            {detail.related.map((link) => (
              <li key={link.finding.id}>
                <span className="muted small">
                  {link.direction === "outgoing" ? "this finding →" : "← linked from"} {link.relation}
                </span>{" "}
                <button className="link" onClick={() => onOpen(link.finding.id)}>
                  {link.finding.title}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
