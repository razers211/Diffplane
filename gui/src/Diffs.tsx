import { useEffect, useState } from "react";
import { fetchDiffJob, fetchDiffJobs, startDiffJob } from "./api";
import { DiffBadge, DiffText } from "./DiffView";
import { diffStats } from "./diff";
import { useCan } from "./role";
import { Tiles } from "./Tiles";
import { naturalCompare } from "./topology";
import { useFetch } from "./useFetch";

const POLL_MS = 3000;

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function DiffJobs({ site }: { site: string }) {
  const { data, error, reload } = useFetch(fetchDiffJobs, []);
  const canOperate = useCan("operator");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const jobs = (data ?? []).filter((j) => !site || j.site === site || j.site === null);
  const running = jobs.some((j) => j.status === "running");

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(id);
  }, [running, reload]);

  const start = async () => {
    setStarting(true);
    setStartError(null);
    try {
      const { id } = await startDiffJob(site ? { site } : {});
      window.location.hash = `#/changes/checks/${id}`;
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <section>
      <div className="toolbar">
        <h1>Diffs</h1>
        {canOperate && (
          <button className="primary" onClick={start} disabled={starting}>
            {starting ? "Starting…" : `Run diff${site ? ` for ${site}` : " for all fabrics"}`}
          </button>
        )}
      </div>
      <p className="muted">
        Compares each active device's running config with the config generated from NetBox. Read-only: nothing is sent to
        the devices.
      </p>
      {startError && <p className="error">{startError}</p>}
      {error && <p className="error">Could not load jobs: {error}</p>}
      {data && jobs.length === 0 && <p className="muted">No diff has been run yet.</p>}
      {jobs.length > 0 && (
        <table className="devices" data-tour="check-list">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Result</th>
              <th>Started</th>
              <th>Fabric</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} className="clickable" onClick={() => (window.location.hash = `#/changes/checks/${job.id}`)}>
                <td>
                  <a href={`#/changes/checks/${job.id}`}><strong>#{job.id}</strong></a>{" "}
                  {job.kind === "drift" ? <span className="chip">scheduled</span> : <span className="chip accent">by {job.requested_by ?? "—"}</span>}
                </td>
                <td>
                  <span className={`pill job-${job.status}`}>{job.status}</span>
                </td>
                <td>
                  {job.status === "running" ? (
                    <span className="faint">{count(job.devices, "switch")}…</span>
                  ) : (
                    <>
                      {job.changed > 0 ? <span className="chip warn">{job.changed} with changes</span> : <span className="chip good">all in sync</span>}{" "}
                      {job.errors > 0 && <span className="chip bad">{count(job.errors, "error")}</span>}{" "}
                      <span className="faint small">of {count(job.devices, "switch")}</span>
                    </>
                  )}
                </td>
                <td>{when(job.created_at)}</td>
                <td>{job.site ?? "all"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function DiffJobDetail({ id }: { id: number }) {
  const { data, error, reload } = useFetch(() => fetchDiffJob(id), [id]);

  useEffect(() => {
    if (data?.status !== "running") return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [data?.status, reload]);

  const snaps = [...(data?.snapshots ?? [])].sort((a, b) => naturalCompare(a.device, b.device));
  const changed = snaps.filter((s) => s.status === "error" || s.has_changes);
  const clean = snaps.filter((s) => s.status === "ok" && !s.has_changes);
  const pending = snaps.filter((s) => s.status === "pending");

  return (
    <div className="page">
    {data && (
      <Tiles
        label="Diff results"
        tiles={[
          { key: "devices", label: "Switches", value: data.devices, sub: data.site ? `fabric ${data.site}` : "all fabrics", tone: "info", icon: "devices" },
          { key: "changed", label: "With changes", value: data.changed, sub: data.changed ? "differ from their intent" : "none", tone: data.changed ? "warn" : "good", icon: "diffs" },
          { key: "errors", label: "Errors", value: data.errors, sub: data.errors ? "could not be read" : "none", tone: data.errors ? "bad" : "good", icon: "alerts" },
        ]}
      />
    )}
    <section>
      <div className="toolbar">
        <a href="#/changes/history">← History</a>
        <h1>Diff job #{id}</h1>
        {data && <span className={`pill job-${data.status}`}>{data.status}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <p className="muted">
          {data.site ? `Fabric ${data.site}` : "All fabrics"} · started {when(data.created_at)} ·{" "}
          {count(data.devices, "device")}, {data.changed} with changes, {count(data.errors, "error")}
        </p>
      )}
      {data?.error && <p className="error">{data.error}</p>}
      {pending.length > 0 && <p className="muted">Still reading {count(pending.length, "switch")}…</p>}
      {changed.map((snap) => {
        const stats = diffStats(snap.diff);
        return (
          <details key={snap.device} className="patch-card" open={changed.length <= 5}>
            <summary>
              <span className="chev" aria-hidden="true" />
              <a className="mono" href={`#/switches/${encodeURIComponent(snap.device)}`} onClick={(e) => e.stopPropagation()}>{snap.device}</a>
              <DiffBadge snap={snap} />
              {snap.status === "ok" && (
                <span className="counts">
                  <span className="add-count">+{stats.added}</span> <span className="remove-count">−{stats.removed}</span>
                </span>
              )}
            </summary>
            <div className="patch-body"><DiffText snap={snap} /></div>
          </details>
        );
      })}
      {clean.length > 0 && (
        <details className="patch-card">
          <summary>
            <span className="chev" aria-hidden="true" />
            <span>{count(clean.length, "switch")} match their intent</span>
            <span className="pill good">no changes</span>
          </summary>
          <p className="patch-body mono small">{clean.map((s) => s.device).join(", ")}</p>
        </details>
      )}
    </section>
    </div>
  );
}
