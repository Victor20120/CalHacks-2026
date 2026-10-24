import { useEffect, useState } from "react";
import { backend } from "../api.js";
import { ErrorNote } from "../components.jsx";

// Group leader only: hand a leaving member's eligible work to the Super Brain, verify it, retire their agent.
export default function OffboardingView({ session }) {
  const [policy, setPolicy] = useState("");
  const [members, setMembers] = useState([]);
  const [history, setHistory] = useState([]);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    try {
      const [p, m, h] = await Promise.all([
        backend("/policy"),
        backend("/members", { token: session.token }),
        backend("/offboarding", { token: session.token }),
      ]);
      setPolicy(p.offboarding);
      setMembers(m);
      setHistory(h);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function offboard(member) {
    const question =
      `Offboard ${member.display_name}?\n\nTheir access ends now. Their eligible project work is handed to the Super ` +
      "Brain and verified, then their personal agent is retired.";
    if (!window.confirm(question)) return;
    setBusy(true);
    setError("");
    try {
      setReport(await backend("/offboarding", { method: "POST", body: { member_id: member.id }, token: session.token }));
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view">
      <h2>Offboarding</h2>
      <p className="notice">{policy}</p>
      <ErrorNote error={error} />

      <section className="card">
        <h3>Members</h3>
        <table className="file-table">
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>
                  <strong>{m.display_name}</strong> <span className="muted small">{m.role}</span>
                </td>
                <td>{m.status}</td>
                <td>
                  {m.role === "researcher" && m.status === "active" && (
                    <button onClick={() => offboard(m)} disabled={busy}>
                      Offboard
                    </button>
                  )}
                  {m.role === "researcher" && m.status === "offboarded" && !history.some((h) => h.member_id === m.id && h.status === "completed") && (
                    <button onClick={() => offboard(m)} disabled={busy}>
                      Retry handoff
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {report && (
        <section className="card">
          <h3>
            Handoff for {report.member_name}:{" "}
            <span className={report.status === "completed" ? "success-text" : "error-text"}>{report.status}</span>
          </h3>
          <ol className="steps">
            {report.steps.map((s, i) => (
              <li key={i} className={s.done ? "done" : "not-done"}>
                {s.done ? "✓" : "✗"} {s.step}
              </li>
            ))}
          </ol>
          {report.error && <ErrorNote error={report.error} />}
          <p className="muted small">{report.note}</p>
        </section>
      )}

      {history.length > 0 && (
        <section className="card">
          <h3>Past handoffs</h3>
          <table className="file-table">
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td>{h.member_name}</td>
                  <td>{h.status}</td>
                  <td>
                    {h.records_transferred} of {h.records_prepared} record(s) transferred
                  </td>
                  <td className="muted small">{h.started_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
