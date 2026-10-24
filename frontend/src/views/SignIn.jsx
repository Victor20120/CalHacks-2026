import { useEffect, useState } from "react";
import { backend } from "../api.js";
import { ErrorNote } from "../components.jsx";

export default function SignIn({ onSignIn }) {
  const [members, setMembers] = useState([]);
  const [policy, setPolicy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    backend("/demo/members")
      .then(setMembers)
      .catch(() => setError("Can't reach the backend. Start it with: python -m uvicorn backend.api.app:app --port 8000"));
    backend("/policy")
      .then((p) => setPolicy(p.offboarding))
      .catch(() => {});
  }, []);

  async function choose(id) {
    try {
      await onSignIn(id);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="sign-in">
      <h1>SimGuard</h1>
      <p className="muted">Prototype sign-in: choose a demo member.</p>
      <div className="member-list">
        {members.map((m) => (
          <button key={m.id} onClick={() => choose(m.id)}>
            <strong>{m.display_name}</strong>
            <span className="muted small">{m.role}</span>
          </button>
        ))}
      </div>
      <ErrorNote error={error} />
      {policy && <p className="muted small policy">{policy}</p>}
    </div>
  );
}
