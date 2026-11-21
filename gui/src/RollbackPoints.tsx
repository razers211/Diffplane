import { useState } from "react";
import { applyRollback, fetchRollbackPoints, previewRollback, type RollbackPlan, type RollbackResult } from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Rollback points: the fabric's intent at each verified deploy. Rolling back
// changes intent only (then review, approve and deploy as usual): for one switch (its attachments,
// trunks, services and routing, plus segments they need) or for the whole fabric.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function RollbackPoints({ site, device }: { site: string; device?: string }) {
  const points = useFetch(() => (site ? fetchRollbackPoints(site) : Promise.resolve([])), [site]);
  const [plan, setPlan] = useState<RollbackPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<RollbackResult | null>(null);
  const canOperate = useCan("operator");

  const restore = async () => {
    if (!plan) return;
    setError(null);
    setBusy(plan.snapshot.id);
    try {
      const r = await applyRollback(site, plan.snapshot.id, plan.operations.length, device);
      setResult(r);
      setPlan(null);
      setConfirming(false);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(null);
    }
  };

  const preview = async (id: number) => {
    setError(null);
    setBusy(id);
    try {
      setPlan(await previewRollback(site, id, device));
      setResult(null);
      setConfirming(false);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="panel" data-tour="rollback-points">
      <header>
        <h2>Rollback points</h2>
        <span className="hint">{device ? `${device}'s intent` : "the whole fabric's intent"} as at each verified deploy</span>
      </header>
      {points.error && <p className="error empty">{points.error}</p>}
      {(points.data ?? []).length === 0 ? (
        <p className="empty muted">None yet: a rollback point is kept when a deploy of {site || "the fabric"} is verified.</p>
      ) : (
        <div className="tablewrap">
          <table className="devices">
            <thead><tr><th>Point</th><th>Deploy</th><th>Kept</th><th>Holds</th><th /></tr></thead>
            <tbody>
              {(points.data ?? []).map((p) => (
                <tr key={p.id} className={plan?.snapshot.id === p.id ? "selected" : ""}>
                  <td className="mono">#{p.id}</td>
                  <td>{p.job_id !== null ? <a href={`#/changes/deploys/${p.job_id}`}>#{p.job_id}</a> : "—"}</td>
                  <td>{new Date(p.taken_at).toLocaleString()}</td>
                  <td className="small muted">
                    {p.segments} segments · {p.services} services · {p.trunks} trunks · {p.routing_targets} routing targets
                  </td>
                  <td><button className="small" onClick={() => preview(p.id)} disabled={busy !== null}>
                    {busy === p.id ? "Comparing…" : "Preview rollback"}
                  </button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error && <p className="error empty">{error}</p>}
      {plan && (
        <div className="empty rollback-plan">
          <div className="toolbar">
            <b>Back to point #{plan.snapshot.id}{Array.isArray(plan.scope) ? ` for ${plan.scope.join(" and ")}` : " for the whole fabric"}</b>
            <button className="small" onClick={() => setPlan(null)}>Close</button>
          </div>
          {plan.operations.length === 0 ? (
            <p className="muted">Intent already matches this point: nothing to change.</p>
          ) : (
            <ol className="rollback-ops">
              {plan.operations.map((o, i) => <li key={i} className={`op-${o.kind}`}>{o.text}</li>)}
            </ol>
          )}
          {plan.notes.map((n, i) => <p key={i} className="notice small">{n}</p>)}
          {plan.operations.length > 0 && canOperate && (
            confirming ? (
              <p className="notice">
                Change intent with these {plan.operations.length} operations? Nothing reaches a switch until the change is
                reviewed, approved and deployed.{" "}
                <button className="small primary" onClick={restore} disabled={busy !== null}>{busy !== null ? "Restoring…" : "Restore"}</button>{" "}
                <button className="small" onClick={() => setConfirming(false)}>Cancel</button>
              </p>
            ) : (
              <button className="primary" onClick={() => setConfirming(true)}>Restore this intent…</button>
            )
          )}
          <p className="muted small">
            Restoring changes intent only; then the change is reviewed, approved and deployed as usual (the bar above shows it).
          </p>
        </div>
      )}
      {result && (
        <div className="empty rollback-plan">
          <div className="toolbar">
            <b>{result.failed ? "Rollback stopped" : "Intent restored"}: {result.done.length} of {result.operations.length} operations</b>
            <button className="small" onClick={() => setResult(null)}>Close</button>
          </div>
          {result.failed && (
            <p className="error">
              Stopped at “{result.failed.text}”: {result.failed.error}. What was done stays in intent; fix the cause and preview again.
            </p>
          )}
          <p className="muted small">
            Next: <a href="#/changes">review the patch and deploy</a>.
          </p>
        </div>
      )}
    </div>
  );
}
