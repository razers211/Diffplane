import { Term } from "./Term";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAudit, fetchDeployJob, fetchDeployJobs, fetchDevices, fetchDiffJob, fetchDrift, requestDeploy, startDiffJob,
  type AuditEvent, type DeployJob, type DriftDevice,
} from "./api";
import { barText, DEPLOYS_CHANGED, describe, INTENT_SAVED, intentChanges, pending, STEPS, type Pending } from "./changes";
import { DeployJobDetail } from "./Deploys";
import { ChangeSetsPanel } from "./Draft";
import { windowText } from "./windowPlan";
import { FabricChecksPanel } from "./Checks";
import { DiffText } from "./DiffView";
import { href } from "./nav";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Changes: one guided path from a saved change to the switches.
// Saved -> Review the patch -> Approve -> Deploy and verify. Deploy rules are unchanged.

const POLL_MS = 15_000;
const SETTLE_MS = 4_000;     // one check after a burst of saves
const CHECK_POLL_MS = 2_000;
const CHECK_LIMIT_MS = 180_000;
const RECENT_MS = 5 * 60_000;

export interface PendingState {
  state: Pending | null;
  drift: DriftDevice[];
  deploys: DeployJob[];
  checking: boolean;        // a check started after a save is running
  scheduler: { last_started: number | null; next_run: number | null; interval_seconds: number } | null;
  reload: () => void;
}

/** What waits on the selected fabric, kept current: polled, and checked again after each save. */
export function usePending(site: string, signedIn: boolean, canOperate: boolean): PendingState {
  const drift = useFetch(() => (signedIn ? fetchDrift() : Promise.resolve(null)), [signedIn]);
  const deploys = useFetch(() => (signedIn ? fetchDeployJobs() : Promise.resolve(null)), [signedIn]);
  const [checking, setChecking] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const reload = useCallback(() => { drift.reload(); deploys.reload(); }, [drift.reload, deploys.reload]);

  useEffect(() => {
    if (!signedIn) return;
    const id = window.setInterval(reload, POLL_MS);
    window.addEventListener(DEPLOYS_CHANGED, reload);
    return () => { window.clearInterval(id); window.removeEventListener(DEPLOYS_CHANGED, reload); };
  }, [signedIn, reload]);

  useEffect(() => {
    if (!canOperate) return;   // viewers save nothing; operators may run diffs
    const check = async () => {
      setChecking(true);
      try {
        const { id } = await startDiffJob(site ? { site } : {});
        const until = Date.now() + CHECK_LIMIT_MS;
        while (Date.now() < until && (await fetchDiffJob(id)).status === "running") {
          await new Promise((r) => window.setTimeout(r, CHECK_POLL_MS));
        }
      } catch {
        // the next drift run catches up
      } finally {
        setChecking(false);
        reload();
      }
    };
    const onSaved = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(check, SETTLE_MS);
    };
    window.addEventListener(INTENT_SAVED, onSaved);
    return () => {
      window.removeEventListener(INTENT_SAVED, onSaved);
      window.clearTimeout(timer.current);
    };
  }, [site, canOperate, reload]);

  const ready = drift.data && deploys.data;
  return {
    state: ready ? pending(site, drift.data!.devices, deploys.data!) : null,
    drift: drift.data?.devices ?? [],
    deploys: deploys.data ?? [],
    checking,
    scheduler: drift.data?.scheduler ?? null,
    reload,
  };
}

export function PendingBar({ p }: { p: PendingState }) {
  const text = p.state ? barText(p.state) : null;
  if (!text && !p.checking) return null;
  return (
    <div className={`pending-bar${text ? "" : " quiet"}`} role="status">
      <span className="dot" aria-hidden="true" />
      <span className="grow">{text ?? "Checking the switches against your change…"}</span>
      {text && <a className="button primary" href="#/changes">Review and deploy</a>}
    </div>
  );
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString();
}

function Stepper({ state }: { state: Pending }) {
  const at = STEPS.findIndex((s) => s.step === state.step);
  const hint: Record<string, string> = {
    saved: "intent written to NetBox",
    review: state.preparing ? "computing each switch's patch…" : "read the differences, then prepare the deploy",
    approve: "an approver reads every patch and approves",
    deploy: "in waves, never both switches of a pair, each verified",
  };
  return (
    <ol className="change-steps">
      {STEPS.map((s, i) => (
        <li key={s.step} className={i < at ? "done" : i === at ? "now" : ""}>
          <span className="stepno">{i + 1}{i < at ? " · done" : i === at ? " · now" : ""}</span>
          <b>{s.label}</b>
          <span>{hint[s.step]}</span>
        </li>
      ))}
    </ol>
  );
}

export function ChangesPending({ site, p }: { site: string; p: PendingState }) {
  const canOperate = useCan("operator");
  const audit = useFetch(fetchAudit, [p.drift, p.deploys]);   // refreshed with every poll and after each check
  const switches = useFetch(() => fetchDevices(site || undefined), [site]);
  const [generators, setGenerators] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const state = p.state;
  const job = state?.rolling ?? state?.awaiting ?? state?.preparing ?? null;
  // The last deploy stays in view for a while after it ends: its result, on the map too.
  const recent = job ? null : p.deploys.find((j) => (!site || j.site === site || j.site === null)
    && ["verified", "failed", "unchanged"].includes(j.status) && !!j.finished_at && Date.now() - Date.parse(j.finished_at) < RECENT_MS) ?? null;
  const changes = intentChanges(site, audit.data ?? [], p.deploys, (switches.data ?? []).map((d) => d.name));

  const act = async (what: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await what();
      p.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    } finally {
      setBusy(false);
    }
  };
  const prepare = () => act(async () => {
    const { id } = await requestDeploy({
      ...(site ? { site } : {}),
      ...(advanced && generators.trim() ? { generators: generators.split(",").map((g) => g.trim()).filter(Boolean) } : {}),
    });
    p.reload();
    // A deploy with nothing to send leaves the switches' last check as it was: check again, so a
    // switch listed as differing from an older check does not stay listed (and Prepare loop).
    const until = Date.now() + CHECK_LIMIT_MS;
    let job = await fetchDeployJob(id);
    while (["queued", "preparing"].includes(job.status) && Date.now() < until) {
      await new Promise((r) => window.setTimeout(r, CHECK_POLL_MS));
      job = await fetchDeployJob(id);
    }
    if (job.status === "unchanged") await startDiffJob(site ? { site } : {});
  });
  const check = () => act(() => startDiffJob(site ? { site } : {}));

  if (!state) return <p className="muted">Loading…</p>;
  return (
    <div className="page changes">
      <div className="toolbar">
        <h1 className="sr-only">Changes</h1>
        {canOperate && !job && (
          <button onClick={check} disabled={busy || p.checking}>{p.checking ? "Checking…" : "Check the switches now"}</button>
        )}
      </div>
      <p className="muted">
        A change saved here reaches the switches only through a <Term k="deploy">deploy</Term>: the platform computes each
        switch's <Term k="patch">patch</Term>, an approver reads and approves it, then it is sent in waves (never both switches of a pair at once) and each switch is verified.
        {p.scheduler && <> The switches are also compared with intent every {Math.round(p.scheduler.interval_seconds / 60)} min.</>}
      </p>
      {error && <p className="error">{error}</p>}

      {state.step === null && state.failed.length === 0 ? (
        <>
          <p className="notice">Nothing to deploy: every switch of {site || "every fabric"} matches its intent.</p>
          {changes.length > 0 && (
            <section className="panel" aria-label="Intent changes">
              <header><h2>Saved since the last deploy</h2><span className="hint">none of these changes a switch's config</span></header>
              <p className="empty">
                These changes are in intent, but the switches' config does not depend on them (for example a segment's name,
                which reaches a switch only as a routed segment's VPN instance). Nothing has to be deployed for them.
              </p>
              <ChangeList changes={changes} />
            </section>
          )}
        </>
      ) : (
        state.step && <Stepper state={state} />
      )}

      {job ? (
        <DeployJobDetail id={job.id} embedded />
      ) : (
        (state.outOfSync.length > 0 || state.failed.length > 0) && (
          <>
            <div className="cols">
              <section className="panel" aria-label="Intent changes">
                <header><h2>What changed in intent</h2><span className="hint">since the last verified deploy</span></header>
                {changes.length === 0 ? (
                  <p className="empty">No saved change since then: the differences on the right were made on the switches, or before that deploy.</p>
                ) : (
                  <ChangeList changes={changes} />
                )}
              </section>
              <section className="panel" aria-label="Switches that differ">
                <header><h2>Switches that differ</h2><span className="hint">{state.outOfSync.length + state.failed.length}</span></header>
                <ul className="changelist">
                  {[...state.outOfSync, ...state.failed].map((d) => (
                    <li key={d.device}>
                      <span>
                        <a className="mono" href={href.switchPage(d.device, "config")}>{d.device}</a>{" "}
                        <span className={d.state === "error" ? "pill bad" : "pill warnpill"}>{d.state === "error" ? "could not be checked" : "differs"}</span>
                      </span>
                      <button className="small" onClick={() => setOpen(open === d.device ? null : d.device)}>
                        {open === d.device ? "Hide" : d.state === "error" ? "Error" : "Diff"}
                      </button>
                      {open === d.device && (
                        <DiffText snap={{
                          device: d.device, status: d.state === "error" ? "error" : "ok", has_changes: d.state === "out_of_sync",
                          diff: d.diff, error: d.error, created_at: d.checked_at ?? "", finished_at: d.checked_at,
                          job_id: d.job_id ?? 0, job_kind: d.job_kind,
                        }} />
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            </div>
            {site && <FabricChecksPanel site={site} />}
            {canOperate && state.outOfSync.length > 0 && (
              <div className="actions-row">
                <button className="linklike small" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>
                  {advanced ? "▾" : "▸"} Advanced
                </button>
                {advanced && (
                  <input id="deploy-generators" className="generators" placeholder="only these generators, e.g. Snmp,Ntp"
                    value={generators} onChange={(e) => setGenerators(e.target.value)} />
                )}
                <button className="primary" onClick={prepare} disabled={busy}>
                  {busy ? "Preparing…" : `Prepare the deploy${site ? ` of ${site}` : ""}`}
                </button>
              </div>
            )}
          </>
        )
      )}
      {state.scheduled.length > 0 && (
        <section className="panel" aria-label="Scheduled deploys">
          <header><h2>Scheduled for a window</h2><span className="hint">others may deploy meanwhile</span></header>
          <ul className="changelist">
            {state.scheduled.map((j) => (
              <li key={j.id}>
                <a href={`#/changes/deploys/${j.id}`}><b>Deploy #{j.id}</b></a>{" "}
                {j.window_start && j.window_end && windowText(new Date(j.window_start), new Date(j.window_end))}
                <span className="sub">approved by {j.approved_by ?? "—"}{j.params?.change_set ? ` · draft #${j.params.change_set}` : ""}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {site && <ChangeSetsPanel site={site} />}
      {recent && (
        <section className="recent-deploy" aria-label="Last deploy">
          <p className="muted small">Last deploy, ended {when(recent.finished_at)}:</p>
          <DeployJobDetail id={recent.id} embedded />
        </section>
      )}
    </div>
  );
}

function ChangeList({ changes }: { changes: AuditEvent[] }) {
  return (
    <ul className="changelist">
      {changes.map((e, i) => (
        <li key={i}>
          <b>{describe(e.action)}</b> <span className="mono">{e.detail}</span>
          <span className="sub">{e.user ?? "—"} · {when(e.at)}</span>
        </li>
      ))}
    </ul>
  );
}

