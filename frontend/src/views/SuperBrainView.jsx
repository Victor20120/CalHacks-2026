import { useEffect, useState } from "react";
import { backend } from "../api.js";
import AgentChat from "../AgentChat.jsx";
import { ErrorNote, Markdown, VerificationBadge } from "../components.jsx";

const CATEGORY_LABELS = {
  error_diagnosis: "Error diagnosis",
  research_result: "Research result",
  code_change: "Code change",
};

// Shared updates grouped by researcher, with private CREAO chat docked at the bottom.
export default function SuperBrainView({ session, project, projects }) {
  const [findings, setFindings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    backend(`/superbrain/findings?project_id=${encodeURIComponent(project.id)}`, { token: session.token })
      .then((list) => {
        setFindings(list);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [project.id]);

  const projectName = (id) => projects.find((p) => p.id === id)?.name || id;
  return (
    <div className="super-brain">
      <div className="sb-content">
        <div className="sb-inner">
          {openId ? (
            <FindingDetail session={session} id={openId} projectName={projectName} onOpen={setOpenId} onBack={() => setOpenId(null)} />
          ) : (
            <Overview
              findings={findings}
              loading={loading}
              project={project}
              projectName={projectName}
              onOpen={setOpenId}
              error={error}
            />
          )}
        </div>
      </div>
      <AgentChat key={`${session.member.id}:${project.id}`} session={session} project={project} onOpenFinding={setOpenId} />
    </div>
  );
}

function Overview({ findings, loading, project, projectName, onOpen, error }) {
  const list = findings || [];
  const researchers = byResearcher(list);
  const verified = list.filter((f) => f.verification_status === "verified").length;
  const hypotheses = list.filter((f) => f.verification_status === "hypothesis").length;

  return (
    <div className={loading && findings ? "refreshing" : ""}>
      <h2>Super Brain</h2>
      <p className="muted">Updates your research group has shared in {project.name}.</p>
      <ErrorNote error={error} />

      <div className="stat-row">
        <Stat label="Updates shared" value={list.length} />
        <Stat label="Researchers sharing" value={researchers.length} />
        <Stat label="Verified by a researcher" value={verified} />
        <Stat label="Still hypotheses" value={hypotheses} />
      </div>

      {findings && !list.length && (
        <section className="card">
          <p className="muted">Nothing has been shared in {project.name} yet.</p>
        </section>
      )}

      {researchers.length >= 2 && <UpdatesChart researchers={researchers} projectName={projectName} />}

      {researchers.map((r) => (
        <ResearcherUpdates key={r.id} researcher={r} projectName={projectName} onOpen={onOpen} />
      ))}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

// One series (updates per researcher): one colour, no legend, value at the bar tip.
// The researcher sections below carry the same numbers, so the tooltip never gates them.
function UpdatesChart({ researchers, projectName }) {
  const [tip, setTip] = useState(null);
  const most = Math.max(...researchers.map((r) => r.findings.length));

  function jumpTo(id) {
    document.getElementById(`researcher-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <section className="card">
      <h3>Updates shared per researcher</h3>
      <ul className="bar-chart">
        {researchers.map((r) => {
          const count = r.findings.length;
          const statuses = countBy(r.findings, (f) => f.verification_status);
          return (
            <li key={r.id} onMouseEnter={() => setTip(r.id)} onMouseLeave={() => setTip(null)}>
              <button
                className="bar-row"
                onClick={() => jumpTo(r.id)}
                onFocus={() => setTip(r.id)}
                onBlur={() => setTip(null)}
                aria-label={`${r.name}: ${plural(count, "update")}. Show their updates.`}
              >
                <span className="bar-label">{r.name}</span>
                <span className="bar-track">
                  <span className="bar" style={{ width: `calc((100% - 3rem) * ${count / most})` }} />
                  <span className="bar-value">{count}</span>
                </span>
              </button>
              {tip === r.id && (
                <div className="chart-tip" role="tooltip">
                  <strong>{plural(count, "update")}</strong>
                  <span>{Object.entries(statuses).map(([s, n]) => `${n} ${s}`).join(" · ")}</span>
                  <span className="muted">Latest {r.findings[0].approved_at.slice(0, 10)}</span>
                  <span className="muted">{[...new Set(r.findings.map((f) => projectName(f.project_id)))].join(", ")}</span>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ResearcherUpdates({ researcher, projectName, onOpen }) {
  const count = researcher.findings.length;
  return (
    <section className="card researcher" id={`researcher-${researcher.id}`}>
      <div className="card-header">
        <h3>{researcher.name}</h3>
        <span className="muted small">
          {plural(count, "update")} · latest {researcher.findings[0].approved_at.slice(0, 10)}
        </span>
      </div>
      <ul className="update-list">
        {researcher.findings.map((f) => (
          <UpdateItem key={f.id} finding={f} projectName={projectName} onOpen={onOpen} />
        ))}
      </ul>
    </section>
  );
}

function UpdateItem({ finding, projectName, onOpen, showAuthor }) {
  const issue = issueOf(finding.summary);
  return (
    <li>
      <div className="update-head">
        <button className="link update-title" onClick={() => onOpen(finding.id)}>
          {finding.title}
        </button>
        <VerificationBadge status={finding.verification_status} />
      </div>
      {issue && (
        <p className="update-issue">
          <span className="label">Issue</span>
          {issue}
        </p>
      )}
      <div className="small muted">
        {showAuthor && `${finding.contributed_by_name} · `}
        {CATEGORY_LABELS[finding.category] || finding.category} · {projectName(finding.project_id)} ·{" "}
        {finding.approved_at.slice(0, 10)} {finding.handed_off && <HandedOffBadge />}
      </div>
    </li>
  );
}

// From an offboarding handoff: shared by group policy, not approved by its author.
function HandedOffBadge() {
  return <span className="badge handed-off">handed off at offboarding</span>;
}

function FindingDetail({ session, id, projectName, onOpen, onBack }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setDetail(null);
    backend(`/findings/${id}`, { token: session.token })
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [id]);

  return (
    <div>
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
              {CATEGORY_LABELS[detail.finding.category] || detail.finding.category} ·{" "}
              {projectName(detail.finding.project_id)} · shared by {detail.finding.contributed_by_name} on{" "}
              {detail.finding.approved_at.slice(0, 10)}
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

// Researchers with the most updates first; each one's updates stay newest first.
function byResearcher(findings) {
  const groups = new Map();
  for (const f of findings) {
    if (!groups.has(f.contributed_by)) groups.set(f.contributed_by, { id: f.contributed_by, name: f.contributed_by_name, findings: [] });
    groups.get(f.contributed_by).findings.push(f);
  }
  return [...groups.values()].sort((a, b) => b.findings.length - a.findings.length || a.name.localeCompare(b.name));
}

function countBy(items, key) {
  const counts = {};
  for (const item of items) counts[key(item)] = (counts[key(item)] || 0) + 1;
  return counts;
}

// The "Symptom:" line of a shared summary, else its first line, as plain text.
function issueOf(summary) {
  const lines = (summary || "").split("\n").map((l) => l.replace(/[*`#]/g, "").replace(/^\s*-\s+/, "").trim());
  const symptom = lines.find((l) => /^symptom:/i.test(l));
  const text = (symptom ? symptom.replace(/^symptom:\s*/i, "") : lines.find(Boolean)) || "";
  return text.length > 220 ? `${text.slice(0, 217)}…` : text;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
