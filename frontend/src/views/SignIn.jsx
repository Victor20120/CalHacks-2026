import { useEffect, useState } from "react";
import { ChevronRight, Crown, Microscope, Network } from "lucide-react";
import { backend } from "../api.js";
import { Avatar, ErrorNote } from "../components.jsx";

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
      <div className="brand">
        <span className="brand-mark"><Network /></span>
        <span>
          <h1 className="brand-name">ExpandRG</h1>
          <span className="tagline">Expand Researching Globally</span>
        </span>
      </div>
      <section className="card">
        <h3>Sign in</h3>
        <p className="muted small">Prototype sign-in: choose a member.</p>
        <div className="member-list">
          {members.map((m) => (
            <button key={m.id} onClick={() => choose(m.id)}>
              <Avatar id={m.id} name={m.display_name} />
              <span className="member-text">
                <strong>{m.display_name}</strong>
                <span className="muted xs">{m.role === "leader" ? <><Crown />Group leader</> : <><Microscope />Researcher</>}</span>
              </span>
              <ChevronRight className="chev" />
            </button>
          ))}
        </div>
        <ErrorNote error={error} />
      </section>
      {policy && <p className="muted small policy">{policy}</p>}
    </div>
  );
}
