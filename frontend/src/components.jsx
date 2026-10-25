import { useEffect, useState } from "react";
import { CONNECTOR_HELP, connector } from "./api.js";

// A size the viewer chose by dragging, kept in this browser only. null means the layout's default.
export function useStoredSize(key) {
  const [size, setSize] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(key));
      return saved > 0 ? saved : null;
    } catch {
      return null;
    }
  });
  function update(value) {
    setSize(value);
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, String(Math.round(value)));
    } catch {
      // storage unavailable: the size lasts until the page reloads
    }
  }
  return [size, update];
}

// A handle that resizes a panel. Drag it, or focus it and use the arrow keys;
// double-click puts the panel back to its default size. current() measures the
// panel now; grow is +1 when moving right/down makes it bigger, -1 when it makes it smaller.
export function Resizer({ axis, current, onResize, min, max, grow = 1, label }) {
  const clamp = (value) => Math.min(max(), Math.max(min, value));
  function start(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    const origin = axis === "x" ? event.clientX : event.clientY;
    const from = current();
    const move = (e) => onResize(clamp(from + grow * ((axis === "x" ? e.clientX : e.clientY) - origin)));
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove(`resizing-${axis}`);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    document.body.classList.add(`resizing-${axis}`);
  }
  function key(event) {
    const keys = axis === "x" ? { ArrowLeft: -1, ArrowRight: 1 } : { ArrowUp: -1, ArrowDown: 1 };
    if (!(event.key in keys)) return;
    event.preventDefault();
    onResize(clamp(current() + grow * keys[event.key] * 16));
  }
  return (
    <div
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={`${label}: drag, or use the arrow keys, to resize. Double-click to reset.`}
      tabIndex={0}
      className={`resizer resizer-${axis}`}
      onPointerDown={start}
      onKeyDown={key}
      onDoubleClick={() => onResize(null)}
    />
  );
}

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

// Pick (or grant) a project folder through the local connector. Folders are shown by
// name; the full path is in the tooltip.
export function FolderPicker({ root, onChange }) {
  const [grants, setGrants] = useState([]);
  const [newFolder, setNewFolder] = useState("");
  const [adding, setAdding] = useState(false);
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
      setAdding(false);
      await load();
      onChange(granted.root);
    } catch (e) {
      setError(e.message);
    }
  }

  // Two folders with the same name are told apart by the folder they're in.
  const label = (g) => (grants.filter((o) => o.name === g.name).length > 1 ? `${g.name} (in ${parentName(g.root)})` : g.name);

  return (
    <div className="folder-picker">
      <div className="folder-row">
        <label>
          Project folder
          <select value={root || ""} onChange={(e) => onChange(e.target.value)} title={root || ""}>
            {!grants.length && <option value="">No folders yet</option>}
            {grants.map((g) => (
              <option key={g.root} value={g.root} title={g.root}>
                {label(g)}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="link" onClick={() => setAdding(!adding)}>
          {adding ? "Cancel" : "Add a folder"}
        </button>
      </div>
      {(adding || !grants.length) && (
        <form className="inline-form" onSubmit={grant}>
          <input
            value={newFolder}
            onChange={(e) => setNewFolder(e.target.value)}
            placeholder={"Folder on this computer, e.g. C:\\research\\heat_solver"}
          />
          <button type="submit" disabled={!newFolder.trim()}>
            Grant access
          </button>
        </form>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

function parentName(path) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 2] : path;
}
