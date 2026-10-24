import { useEffect, useState } from "react";
import { CONNECTOR_HELP, connector } from "./api.js";

export function SeverityBadge({ severity }) {
  return <span className={`badge severity-${severity}`}>{severity}</span>;
}

const VERIFICATION_LABELS = {
  hypothesis: "Hypothesis · not verified",
  supported: "Supported",
  verified: "Verified by a researcher",
  unresolved: "Unresolved",
};

export function VerificationBadge({ status }) {
  return <span className={`badge verification-${status}`}>{VERIFICATION_LABELS[status] || status}</span>;
}

// The agent's own label for an issue. Never styled like researcher verification.
export function AgentStatusBadge({ status, confidence }) {
  return (
    <span className={`badge agent-${status}`}>
      agent: {status}
      {confidence ? ` · ${confidence}` : ""}
    </span>
  );
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return <p className="error-note">{error}</p>;
}

// Renders the agent's **bold**, `code` and "- " list lines as plain React nodes (no HTML injection).
export function Markdown({ text }) {
  const blocks = (text || "").split(/\n{2,}/).filter((b) => b.trim());
  return (
    <div className="markdown">
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (lines.every((l) => /^\s*[-*] /.test(l))) {
          return (
            <ul key={i}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*[-*] /, ""))}</li>
              ))}
            </ul>
          );
        }
        return <p key={i}>{lines.map((l, j) => [j > 0 && <br key={`br${j}`} />, inline(l)])}</p>;
      })}
    </div>
  );
}

function inline(text) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    return part;
  });
}

// Pick (or grant) a project folder through the local connector.
export function FolderPicker({ root, onChange }) {
  const [grants, setGrants] = useState([]);
  const [newFolder, setNewFolder] = useState("");
  const [error, setError] = useState("");

  async function load() {
    try {
      const list = await connector("/grants");
      setGrants(list);
      setError("");
      if (!root && list.length) onChange(list[0].root);
    } catch {
      setError(CONNECTOR_HELP);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function grant(event) {
    event.preventDefault();
    try {
      const granted = await connector("/grants", { method: "POST", body: { root: newFolder } });
      setNewFolder("");
      await load();
      onChange(granted.root);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="folder-picker">
      <label>
        Project folder on this computer
        <select value={root || ""} onChange={(e) => onChange(e.target.value)}>
          {!grants.length && <option value="">No folders granted yet</option>}
          {grants.map((g) => (
            <option key={g.root} value={g.root}>
              {g.name} — {g.root}
            </option>
          ))}
        </select>
      </label>
      <form className="inline-form" onSubmit={grant}>
        <input
          value={newFolder}
          onChange={(e) => setNewFolder(e.target.value)}
          placeholder={"Grant access to another folder, e.g. C:\\research\\heat_solver"}
        />
        <button type="submit" disabled={!newFolder.trim()}>
          Grant access
        </button>
      </form>
      <ErrorNote error={error} />
    </div>
  );
}
