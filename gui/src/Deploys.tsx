import { useEffect, useState } from "react";
import { approveDeploy, fetchAudit, fetchDeployJob, fetchDeployJobs, fetchPasswordJobs, requestDeploy, type DeployJob, type DeployStep } from "./api";
import { patchLines, patchStats } from "./diff";
import { useCan, useMayApprove } from "./role";
import { Findings, useJobChecks } from "./Checks";
import { TopologyView } from "./TopologyView";
import { useFetch } from "./useFetch";
import { WindowPicker, WindowStatus } from "./Windows";
import { windowText } from "./windowPlan";
import { DEPLOYS_CHANGED } from "./changes";

// Deploys: prepare, read each
// device's patch, approve with an explicit confirmation, follow the rollout device by device.

const POLL_MS = 2000;
const ACTIVE = ["queued", "preparing", "approved", "deploying", "scheduled", "reapproval"];

export function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

const LABELS: Record<string, string> = {
  diffed: "awaiting approval",
  queued: "queued behind a rollout",
  pending: "waiting",
  unchanged: "already matches",
  upgrading: "copying the software",   // upgrades
  rebooting: "rebooting",
  scheduled: "scheduled for a window",   // change windows
  reapproval: "needs an approver again",
  cancelled: "cancelled",
  stopped: "stopped at the window's end",
};

export function Status({ status }: { status: string }) {
  return <span className={`pill deploy-${status}`}>{LABELS[status] ?? status}</span>;
}

const changing = (job: DeployJob) => job.steps.filter((s) => s.patch && s.status !== "unchanged");

export function DeployJobs({ site }: { site: string }) {
  const { data, error, reload } = useFetch(fetchDeployJobs, []);
  const canOperate = useCan("operator");
  const [generators, setGenerators] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const passwordJobs = useFetch(fetchPasswordJobs, []);
  const jobs = (data ?? []).filter((j) => !site || j.site === site || j.site === null);
  const passwords = (passwordJobs.data ?? []).filter((j) => !site || j.site === site);
  const active = jobs.some((j) => ACTIVE.includes(j.status));

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(id);
  }, [active, reload]);

  const prepare = async () => {
    setBusy(true);
    setStartError(null);
    try {
      const gens = advanced ? generators.split(",").map((g) => g.trim()).filter(Boolean) : [];
      const { id } = await requestDeploy({ ...(site ? { site } : {}), ...(gens.length ? { generators: gens } : {}) });
      window.location.hash = `#/changes/deploys/${id}`;
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="toolbar">
        <h1 className="sr-only">Deploys</h1>
        {canOperate && (
          <span className="prepare">
            <button className="linklike small" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>
              {advanced ? "▾" : "▸"} Advanced
            </button>
            {advanced && (
              <input
                id="deploy-generators"
                className="generators"
                placeholder="only these generators, e.g. Snmp,Ntp"
                value={generators}
                onChange={(e) => setGenerators(e.target.value)}
              />
            )}
            <button className="primary" onClick={prepare} disabled={busy}>
              {busy ? "Preparing…" : `Prepare deploy${site ? ` for ${site}` : ""}`}
            </button>
          </span>
        )}
      </div>
      <p className="muted">
        Preparing computes each active switch's patch; nothing is sent until someone approves. The rollout then goes one
        switch at a time and stops at the first one that fails verification. A deploy prepared during a rollout is queued.
      </p>
      {startError && <p className="error">{startError}</p>}
      {error && <p className="error">Could not load deploys: {error}</p>}
      {data && jobs.length === 0 && <p className="muted">No deploy yet.</p>}
      {jobs.length > 0 && (
        <table className="devices" data-tour="deploy-list">
          <thead>
            <tr>
              <th>Deploy</th>
              <th>Status</th>
              <th>Changes</th>
              <th>Requested</th>
              <th>Approved by</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const changed = changing(job);
              const totals = changed.reduce((t, s) => {
                const st = patchStats(s.patch);
                return { added: t.added + st.added, removed: t.removed + st.removed };
              }, { added: 0, removed: 0 });
              return (
                <tr key={job.id} className="clickable" onClick={() => (window.location.hash = `#/changes/deploys/${job.id}`)}>
                  <td>
                    <a href={`#/changes/deploys/${job.id}`}><strong>#{job.id}</strong></a>
                    <div className="faint small">{job.site ?? "all fabrics"}{job.generators ? ` · ${job.generators.join(", ")}` : ""}</div>
                  </td>
                  <td>
                    <Status status={job.status} />
                    {job.window_start && job.window_end && (
                      <div className="faint small">window {windowText(new Date(job.window_start), new Date(job.window_end))}</div>
                    )}
                  </td>
                  <td>
                    {changed.length === 0 ? (
                      <span className="faint">{job.steps.length ? "nothing to change" : "—"}</span>
                    ) : (
                      <>
                        {changed.length} of {job.steps.length} switch{job.steps.length === 1 ? "" : "es"}{" "}
                        <span className="add-count">+{totals.added}</span> <span className="remove-count">−{totals.removed}</span>
                      </>
                    )}
                  </td>
                  <td>
                    {when(job.created_at)}
                    <div className="faint small">by {job.requested_by ?? "—"}</div>
                  </td>
                  <td>{job.approved_by ?? <span className="faint">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {passwords.length > 0 && (
        <>
          <h2>SNMP password jobs</h2>
          <table className="devices">
            <thead><tr><th>Job</th><th>Status</th><th>Switches</th><th>Requested</th><th>Approved by</th></tr></thead>
            <tbody>
              {passwords.map((job) => (
                <tr key={job.id}>
                  <td><strong>#{job.id}</strong><div className="faint small">{job.site}</div></td>
                  <td><Status status={job.status} />{job.error && <div className="error small">{job.error}</div>}</td>
                  <td>{job.steps.length}</td>
                  <td>{when(job.created_at)}<div className="faint small">by {job.requested_by ?? "—"}</div></td>
                  <td>{job.approved_by ?? <span className="faint">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

export function DeployJobDetail({ id, embedded = false }: { id: number; embedded?: boolean }) {
  const { data, error, reload } = useFetch(() => fetchDeployJob(id), [id]);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [approving, setApproving] = useState(false);
  const [later, setLater] = useState(false);
  const [span, setSpan] = useState<{ start: Date; end: Date } | null>(null);
  const canApprove = useMayApprove(data?.site);   // approvers, or an operator allowed on this fabric
  const checks = useJobChecks(data?.checks);       // pre-deploy checks
  const active = data && ACTIVE.includes(data.status);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);

  const approve = async (job: DeployJob) => {
    setApproveError(null);
    setApproving(true);
    try {
      const windowed = job.status === "diffed" && (later || !!job.params?.change_set) && span;
      await approveDeploy(job.id, windowed ? { start: span.start.toISOString(), end: span.end.toISOString() } : undefined);
      reload();
      window.dispatchEvent(new Event(DEPLOYS_CHANGED));
    } catch (e) {
      setApproveError(e instanceof Error ? e.message : String(e));
    } finally {
      setApproving(false);
    }
  };

  const changed = data ? changing(data) : [];
  const draft = !!data?.params?.change_set;   // a change set's deploy: only for a window
  const inWindow = later || draft;
  const unchanged = data ? data.steps.filter((s) => s.status === "unchanged") : [];
  const totals = changed.reduce((t, s) => {
    const st = patchStats(s.patch);
    return { added: t.added + st.added, removed: t.removed + st.removed };
  }, { added: 0, removed: 0 });

  return (
    <div className="page">
      <section>
        <div className="toolbar">
          {!embedded && <a href="#/changes/history">← History</a>}
          {embedded ? <h2><a href={`#/changes/deploys/${id}`}>Deploy #{id}</a></h2> : <h1>Deploy #{id}</h1>}
          {data && <Status status={data.status} />}
        </div>
        {error && <p className="error">{error}</p>}
        {data && (
          <p className="muted">
            {data.site ?? "All fabrics"} · requested by {data.requested_by} at {when(data.created_at)}
            {data.approved_by && ` · approved by ${data.approved_by} at ${when(data.approved_at)}`}
            {data.generators && ` · generators: ${data.generators.join(", ")}`}
          </p>
        )}
        {data?.error && <p className={["cancelled", "stopped"].includes(data.status) ? "muted" : "error"}>{data.error}</p>}
        {data && data.steps.length > 0 && <Rollout steps={data.steps} scheduled={data.status === "scheduled"} />}
      </section>
      {data && <WindowStatus job={data} onChanged={reload} />}
      {data?.status === "reapproval" && (
        <section className="approval" aria-label="Approve again">
          <h2>A switch differs at the window's start</h2>
          <p>
            {(data.params?.asked ?? "a patch is not the approved one").replace(/\.?$/, ".")} Read the new patches below and approve them within 15
            minutes of the alert, or nothing more is sent and the changes wait.
          </p>
          {canApprove ? (
            <div className="approve-row">
              <label className="check">
                <input id="reapprove-confirm" type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                I have read the new patches
              </label>
              <button className="primary" disabled={!confirmed || approving} onClick={() => approve(data)}>
                {approving ? "Approving…" : "Approve the new patches"}
              </button>
            </div>
          ) : <p className="muted">Waiting for an approver.</p>}
          {approveError && <p className="error">{approveError}</p>}
          {Object.entries(data.params?.new_patches ?? {}).map(([device, patch]) => (
            <PatchCard key={device} step={{ device, patch, status: "diffed", error: null, started_at: null, finished_at: null }} open />
          ))}
        </section>
      )}

      {data && data.steps.length > 0 && (
        <TopologyView site={data.site ?? ""} title="On the map" deploy={Object.fromEntries(data.steps.map((s) => [s.device, s.status]))} />
      )}

      {data?.status === "diffed" && (
        <section className="approval">
          <h2>Approve this deploy</h2>
          <p>
            <strong>{changed.length}</strong> switch{changed.length === 1 ? "" : "es"} change{" "}
            (<span className="add-count">+{totals.added}</span> <span className="remove-count">−{totals.removed}</span> lines)
            {unchanged.length > 0 && <>, {unchanged.length} already match{unchanged.length === 1 ? "es" : ""}</>}. They are
            deployed in waves (never both switches of a pair, at most half the fabric at once), each verified (empty diff,
            fresh poll, no fabric link lost) before the next wave starts; each patch is computed again just before it is sent
            and must equal the one below.
          </p>
          {checks.known && (
            <div className="job-checks" data-tour="checks">
              <div className="toolbar">
                <b>Checks</b>
                <button className="small" onClick={checks.again} disabled={checks.busy}>{checks.busy ? "Checking…" : "Check again"}</button>
              </div>
              <Findings results={checks.results} />
            </div>
          )}
          {canApprove && checks.blocking ? (
            <p className="error">Approval is blocked by the errors above: fix them in intent, then check again.</p>
          ) : canApprove ? (
            <>
            <span className="toggle when-toggle" role="group" aria-label="When">
              <button aria-pressed={!inWindow} disabled={draft}
                title={draft ? "saved for a window: its changes are applied to intent at the window's start" : undefined}
                onClick={() => setLater(false)}>Now</button>
              <button aria-pressed={inWindow} onClick={() => setLater(true)}>In a change window</button>
            </span>
            {inWindow && <WindowPicker site={data.site ?? ""} onPick={setSpan} />}
            <div className="approve-row">
              <label className="check">
                <input id="deploy-confirm" type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                I have read the patch of every switch below
              </label>
              <button className="primary" disabled={!confirmed || approving || (inWindow && !span)} onClick={() => approve(data)}>
                {approving ? "Approving…" : inWindow
                  ? `Approve for ${span ? windowText(span.start, span.end) : "the window"}`
                  : `Approve and deploy to ${changed.length} switch${changed.length === 1 ? "" : "es"}`}
              </button>
            </div>
            </>
          ) : (
            <p className="muted">Waiting for an approver.</p>
          )}
          {approveError && <p className="error">{approveError}</p>}
        </section>
      )}

      {data && changed.length > 0 && (
        <section data-tour="patches">
          <h2>{data.status === "reapproval" ? "The approved patches" : "Patches"}</h2>
          {changed.map((step) => (
            <PatchCard key={step.device} step={step} open={changed.length <= 3} scheduled={data?.status === "scheduled"} />
          ))}
        </section>
      )}
      {data && unchanged.length > 0 && changed.length === 0 && data.status !== "preparing" && (
        <section>
          <p className="muted">Every switch already matches its intent: nothing to send.</p>
        </section>
      )}

      {data?.log && data.log.length > 0 && (
        <section>
          <details open={data.status === "failed" || !!active}>
            <summary><h2>Log</h2></summary>
            <pre className="config log">
              {data.log.map((line, i) => (
                <div key={i} className={`log-${line.level}`}>
                  {new Date(line.at).toLocaleTimeString()} {line.device ? `[${line.device}] ` : ""}
                  {line.message}
                </div>
              ))}
            </pre>
          </details>
        </section>
      )}
    </div>
  );
}

/** The rollout, device by device, in order. */
export function Rollout({ steps, scheduled = false }: { steps: DeployStep[]; scheduled?: boolean }) {
  return (
    <ol className="rollout" aria-label="Rollout">
      {steps.map((s) => (
        <li key={s.device} className={`step-${s.status}`}>
          <span className="node" aria-hidden="true" />
          <span className="name mono">{s.device}</span>
          <span className="state">{LABELS[scheduled && s.status === "diffed" ? "scheduled" : s.status] ?? s.status}</span>
        </li>
      ))}
    </ol>
  );
}

export function PatchCard({ step, open, scheduled = false }: { step: DeployStep; open: boolean; scheduled?: boolean }) {
  const stats = patchStats(step.patch);
  return (
    <details className="patch-card" open={open}>
      <summary>
        <span className="chev" aria-hidden="true" />
        <a href={`#/switches/${encodeURIComponent(step.device)}`} className="mono" onClick={(e) => e.stopPropagation()}>
          {step.device}
        </a>
        <Status status={scheduled && step.status === "diffed" ? "scheduled" : step.status} />
        <span className="counts">
          <span className="add-count">+{stats.added}</span> <span className="remove-count">−{stats.removed}</span>
        </span>
      </summary>
      {step.error && <pre className="config error-text">{step.error}</pre>}
      {step.patch && (
        <pre className="config diff">
          {patchLines(step.patch).map((line, i) => (
            <div key={i} className={`patch-${line.kind}`}>
              {line.text}
            </div>
          ))}
        </pre>
      )}
    </details>
  );
}

/** An audit action's colour: what kind of change it is. */
export function actionTone(action: string): "accent" | "good" | "bad" | "warn" | "" {
  if (action === "deploy.failed" || action === "upgrade.failed") return "bad";
  if (action === "deploy.verified" || action === "upgrade.verified") return "good";
  if (action === "login.failed") return "warn";
  if (action.startsWith("deploy") || action.startsWith("upgrade") || action.endsWith(".approve")) return "accent";
  if (action.endsWith(".delete") || action.endsWith(".detach") || action.endsWith(".remove")) return "bad";
  if (action.endsWith(".create") || action.endsWith(".attach") || action.endsWith(".import")) return "good";
  if (action.startsWith("auth") || action.startsWith("login") || action.startsWith("user")) return "";
  return "warn";
}

const initialsOf = (name: string | null) =>
  name ? name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("") : "·";

export function Audit() {
  const { data, error } = useFetch(fetchAudit, []);
  const [filter, setFilter] = useState("");
  const rows = (data ?? []).filter((e) => !filter || `${e.user ?? ""} ${e.action} ${e.detail ?? ""}`.toLowerCase().includes(filter.toLowerCase()));
  return (
    <section>
      <div className="toolbar">
        <h1>Audit trail</h1>
      </div>
      <p className="muted">Who changed what, newest first: every intent change, deploy request and approval, and sign-in.</p>
      <div className="filters" data-tour="audit">
        <input id="audit-filter" placeholder="Filter by user, action or detail" value={filter} onChange={(e) => setFilter(e.target.value)} />
        {data && <span className="count">{rows.length} of {data.length}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {rows.length > 0 && (
        <table className="devices">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Action</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e, i) => (
              <tr key={i}>
                <td className="num">{when(e.at)}</td>
                <td>
                  <span className="who-cell">
                    <span className={`mini-avatar${e.user ? "" : " system"}`} aria-hidden="true">{initialsOf(e.user)}</span>
                    {e.user ?? <span className="faint">platform</span>}
                  </span>
                </td>
                <td>
                  <span className={`chip ${actionTone(e.action)}`}>{e.action}</span>
                  {e.job_id && (
                    <>
                      {" "}<a href={e.action.startsWith("upgrade") ? `#/changes/upgrades/${e.job_id}` : `#/changes/deploys/${e.job_id}`}>#{e.job_id}</a>
                    </>
                  )}
                </td>
                <td className="mono small">{e.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && rows.length === 0 && <p className="muted">Nothing matches.</p>}
    </section>
  );
}
