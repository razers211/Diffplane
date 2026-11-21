import type { DiffSnapshot } from "./api";
import { diffLines } from "./diff";

export function DiffBadge({ snap }: { snap: DiffSnapshot }) {
  if (snap.status === "pending") return <span className="pill">running</span>;
  if (snap.status === "error") return <span className="pill bad">error</span>;
  return snap.has_changes ? <span className="pill warnpill">changes</span> : <span className="pill good">no changes</span>;
}

export function DiffText({ snap }: { snap: DiffSnapshot }) {
  if (snap.status === "error") return <pre className="config error-text">{snap.error}</pre>;
  if (snap.status === "pending") return <p className="muted">Fetching the running config…</p>;
  if (!snap.has_changes) return <p className="muted">The device matches NetBox.</p>;
  return (
    <pre className="config diff">
      {diffLines(snap.diff ?? "").map((line, i) => (
        <div key={i} className={`diff-${line.kind}`}>
          {line.text}
        </div>
      ))}
    </pre>
  );
}
