import { useState } from "react";
import { backUpConfig, fetchConfigDiff, fetchConfigVersion, fetchConfigVersions, type ConfigVersion } from "./api";
import { diffLines } from "./diff";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// A switch's kept running configs: after each verified deploy, daily and on
// request; shown with secrets masked. Pick one to read it, or two to compare them.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");
const REASON: Record<ConfigVersion["reason"], string> = { scheduled: "daily", deploy: "after deploy", manual: "by hand" };

export function ConfigHistory({ name, active }: { name: string; active: boolean }) {
  const list = useFetch(() => fetchConfigVersions(name), [name]);
  const canOperate = useCan("operator");
  const [picked, setPicked] = useState<number[]>([]);
  const [view, setView] = useState<{ title: string; text: string; diff: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const versions = list.data?.versions ?? [];

  const pick = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]));
  const open = async () => {
    setMessage(null);
    try {
      if (picked.length === 1) {
        const v = await fetchConfigVersion(name, picked[0]!);
        setView({ title: `#${v.id}, ${new Date(v.taken_at).toLocaleString()}`, text: v.config, diff: false });
      } else {
        const [older, newer] = [...picked].sort((a, b) => a - b) as [number, number];
        const d = await fetchConfigDiff(name, older, newer);
        setView({ title: `#${older} → #${newer}`, text: d.diff || "(no difference)", diff: true });
      }
    } catch (e) {
      setMessage(clean(e));
    }
  };
  const backUp = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const v = await backUpConfig(name);
      setMessage(v.new ? `Kept as version #${v.id}.` : "The running config has not changed since its last version.");
      list.reload();
    } catch (e) {
      setMessage(clean(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" data-tour="config-history">
      <header>
        <h2>History</h2>
        <span className="hint">
          {list.data ? `kept ${list.data.keep_days} days, always the last ${list.data.keep_last}; secrets masked` : ""}
        </span>
        {canOperate && active && (
          <button onClick={backUp} disabled={busy || !!list.data?.problem}>{busy ? "Reading the switch…" : "Back up now"}</button>
        )}
      </header>
      {list.data?.problem && <p className="error empty">{list.data.problem}</p>}
      {list.error && <p className="error empty">{list.error}</p>}
      {message && <p className="notice empty">{message}</p>}
      {versions.length === 0 ? (
        <p className="empty muted">No version kept yet: one is taken after each verified deploy, once a day, or with Back up now.</p>
      ) : (
        <>
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th /><th>Version</th><th>Taken</th><th>Why</th><th>Lines</th></tr></thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.id} className={picked.includes(v.id) ? "selected" : ""}>
                    <td><input type="checkbox" checked={picked.includes(v.id)} onChange={() => pick(v.id)} aria-label={`Pick version ${v.id}`} /></td>
                    <td className="mono">#{v.id}</td>
                    <td>{new Date(v.taken_at).toLocaleString()}</td>
                    <td>
                      {REASON[v.reason]}
                      {v.job_id !== null && <> (<a href={`#/changes/deploys/${v.job_id}`}>deploy #{v.job_id}</a>)</>}
                      {v.requested_by && <span className="muted"> by {v.requested_by}</span>}
                    </td>
                    <td className="num">{v.lines}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="empty">
            <button className="small primary" disabled={picked.length === 0} onClick={open}>
              {picked.length === 2 ? "Compare the two" : "Show this version"}
            </button>{" "}
            <span className="muted small">Pick one version to read it, two to compare them.</span>
          </p>
        </>
      )}
      {view && (
        <div className="empty">
          <div className="toolbar"><b>{view.title}</b><button className="small" onClick={() => setView(null)}>Close</button></div>
          {view.diff ? (
            <pre className="config diff">
              {diffLines(view.text).map((line, i) => <div key={i} className={`diff-${line.kind}`}>{line.text}</div>)}
            </pre>
          ) : (
            <pre className="config">{view.text}</pre>
          )}
        </div>
      )}
    </div>
  );
}
