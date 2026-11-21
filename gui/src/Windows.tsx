import { useEffect, useMemo, useState } from "react";
import { cancelDeploy, fetchUsualWindow, saveUsualWindow, type DeployJob, type Span, type UsualWindow } from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";
import { DEPLOYS_CHANGED } from "./changes";
import {
  clampPick, dayLabel, DAY_MIN, defaultPick, hhmm, insideUsual, MAX_DAYS, pickOf, spanOf, STEP_MIN, until,
  usualDays, windowText, zoneName, type Pick,
} from "./windowPlan";

// Change windows: a deploy approved for a window starts at its start, no new
// wave starts after its end. Times are shown in the browser's zone (named) and sent in UTC.

const errorText = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
const QUARTERS = Array.from({ length: DAY_MIN / STEP_MIN + 1 }, (_, i) => i * STEP_MIN);
const SHORT_MIN = 30;

/** Pick a window within the next 7 days; the fabric's usual window is preset and marked. */
export function WindowPicker({ site, onPick }: { site: string; onPick: (span: { start: Date; end: Date } | null) => void }) {
  const usual = useFetch(() => (site ? fetchUsualWindow(site) : Promise.resolve(null)), [site]);
  const now = useMemo(() => new Date(), []);
  const upcoming = usual.data?.upcoming ?? [];
  const [pick, setPick] = useState<Pick | null>(null);
  const current = pick ?? (usual.loading ? null : defaultPick(upcoming, now));
  const span = current ? spanOf(current, now) : null;

  useEffect(() => { onPick(span); }, [span?.start.getTime(), span?.end.getTime()]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!current || !span) return <p className="muted">Loading the usual window…</p>;

  const set = (p: Pick) => setPick(clampPick(p, now));
  const marked = usualDays(upcoming, now);
  const dayUsual = upcoming.map((u) => pickOf(u, now)).filter((u): u is Pick => u?.day === current.day);
  const outside = usual.data?.usual && !insideUsual(span, upcoming);
  const length = current.end - current.start;
  const pct = (m: number) => `${(m / DAY_MIN) * 100}%`;
  const moveTo = (e: React.MouseEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const start = Math.round(((e.clientX - box.left) / box.width) * DAY_MIN / STEP_MIN) * STEP_MIN;
    set({ ...current, start, end: start + length });
  };

  return (
    <div className="window-picker" data-tour="window-picker">
      <div className="day-chips" role="radiogroup" aria-label="Day">
        {Array.from({ length: MAX_DAYS }, (_, day) => (
          <button key={day} role="radio" aria-checked={day === current.day} className={`chip${day === current.day ? " on" : ""}`}
            onClick={() => set({ ...current, day })}>
            {dayLabel(day, now)}{marked.has(day) && <span className="usual-dot" title="the fabric's usual window" />}
          </button>
        ))}
      </div>
      <div className="day-track" onClick={moveTo} title="Click to move the window, it keeps its length">
        {dayUsual.map((u, i) => <span key={i} className="usual" style={{ left: pct(u.start), width: pct(u.end - u.start) }} />)}
        <span className="pick" style={{ left: pct(current.start), width: pct(length) }} />
        {[0, 6, 12, 18].map((h) => <span key={h} className="tick" style={{ left: pct(h * 60) }}>{hhmm(h * 60)}</span>)}
      </div>
      <div className="form-row">
        <label htmlFor="win-start">
          From
          <select id="win-start" value={current.start} onChange={(e) => set({ ...current, start: +e.target.value, end: +e.target.value + length })}>
            {QUARTERS.slice(0, -1).map((m) => <option key={m} value={m}>{hhmm(m)}</option>)}
          </select>
        </label>
        <label htmlFor="win-end">
          until
          <select id="win-end" value={current.end} onChange={(e) => set({ ...current, end: +e.target.value })}>
            {QUARTERS.filter((m) => m > current.start).map((m) => <option key={m} value={m}>{m === DAY_MIN ? "24:00" : hhmm(m)}</option>)}
          </select>
        </label>
        <span className="muted small">{zoneName()}</span>
      </div>
      <p>
        Starts <b>{windowText(span.start, span.end)}</b> ({zoneName()}), {until(span.start, now)}. No new wave starts after{" "}
        {hhmm(current.end)}; a wave running then finishes and is verified.
      </p>
      {outside && <p className="warning">Outside this fabric's usual window: allowed, but the approval says so.</p>}
      {length < SHORT_MIN && <p className="warning">A short window: each wave takes a few minutes (three waves of seven switches took about 2.5 minutes); what is left waits for the next window.</p>}
    </div>
  );
}

/** A scheduled deploy: its window, a countdown, and Cancel (nothing has been sent). A rolling-out
 *  one: stop after the running wave. */
export function WindowStatus({ job, onChanged }: { job: DeployJob; onChanged: () => void }) {
  const canOperate = useCan("operator");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 30_000);
    return () => window.clearInterval(id);
  }, []);
  const scheduled = job.status === "scheduled";
  const rolling = ["approved", "deploying", "reapproval"].includes(job.status);
  if (!scheduled && !(rolling && job.window_start)) return null;

  const cancel = async () => {
    setError(null);
    try {
      await cancelDeploy(job.id);
      setAsking(false);
      onChanged();
      window.dispatchEvent(new Event(DEPLOYS_CHANGED));
    } catch (e) {
      setError(errorText(e));
    }
  };
  const start = job.window_start ? new Date(job.window_start) : null;
  const end = job.window_end ? new Date(job.window_end) : null;
  return (
    <section className="window-status" aria-label="Change window">
      {start && end && (
        <p>
          {scheduled ? "Approved for the window " : "In the window "}<b>{windowText(start, end)}</b> ({zoneName()})
          {scheduled ? `, ${until(start, new Date())}. Nothing is sent before it.` : `: no new wave starts after ${end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}.`}
          {job.params?.change_set && <> Its changes (draft #{job.params.change_set}) are applied to intent at the start.</>}
        </p>
      )}
      {canOperate && !asking && (
        <button onClick={() => setAsking(true)}>{scheduled ? "Cancel this deploy" : "Stop after this wave"}</button>
      )}
      {asking && (
        <div className="confirm-inline" role="alertdialog" aria-label="Confirm">
          <span>
            {scheduled
              ? "Cancel it? Nothing has been sent; its saved changes go back to their draft."
              : "Stop it? The running wave finishes and is verified, no other wave starts; the remaining switches keep their differences."}
          </span>
          <button className="danger" onClick={cancel}>{scheduled ? "Yes, cancel" : "Yes, stop"}</button>
          <button onClick={() => setAsking(false)}>Keep it</button>
        </div>
      )}
      {canOperate && <p className="faint small">Who requested it, approvers and admins may cancel.</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

const DAY_NAMES: [string, string][] = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const LENGTHS = [30, 60, 120, 180, 240, 360, 480];

/** The fabric's usual window (Fabric page): approvers and admins set it, everyone sees it. */
export function UsualWindowPanel({ site }: { site: string }) {
  const canApprove = useCan("approver");
  const { data, error, reload } = useFetch(() => fetchUsualWindow(site), [site]);
  const [editing, setEditing] = useState<UsualWindow | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const usual = data?.usual ?? null;

  const save = async (value: UsualWindow | null) => {
    setSaveError(null);
    try {
      await saveUsualWindow(site, value);
      setEditing(null);
      reload();
    } catch (e) {
      setSaveError(errorText(e));
    }
  };
  const describe = (u: UsualWindow) =>
    `${u.days.map((d) => DAY_NAMES.find(([k]) => k === d)?.[1] ?? d).join(", ")} at ${u.start} for ${u.minutes >= 60 ? `${u.minutes / 60} h` : `${u.minutes} min`} (${u.zone})`;
  const next: Span | null = data?.next ?? null;

  return (
    <section className="panel ntp-panel" aria-label="Change window" data-tour="usual-window">
      <header><h2>Usual change window</h2><span className="hint">offered when approving</span></header>
      <div className="pad routing">
        {error && <p className="error">{error}</p>}
        {!editing ? (
          <p>
            {usual ? describe(usual) : "None: deploys are approved for now, or for any window chosen then."}{" "}
            {next && <span className="muted">Next: {windowText(new Date(next.start), new Date(next.end))} ({zoneName()}).</span>}{" "}
            {canApprove && <button onClick={() => setEditing(usual ?? { days: ["sat"], start: "02:00", minutes: 180, zone: zoneName() })}>{usual ? "Change" : "Set one"}</button>}
            {canApprove && usual && <button onClick={() => save(null)}>Remove</button>}
          </p>
        ) : (
          <div className="form-row">
            <span className="day-chips" role="group" aria-label="Days">
              {DAY_NAMES.map(([k, label]) => {
                const on = editing.days.includes(k);
                return (
                  <button key={k} aria-pressed={on} className={`chip${on ? " on" : ""}`}
                    onClick={() => setEditing({ ...editing, days: on ? editing.days.filter((d) => d !== k) : [...editing.days, k] })}>{label}</button>
                );
              })}
            </span>
            <label htmlFor="uw-start">
              at
              <select id="uw-start" value={editing.start} onChange={(e) => setEditing({ ...editing, start: e.target.value })}>
                {QUARTERS.slice(0, -1).map((m) => <option key={m} value={hhmm(m)}>{hhmm(m)}</option>)}
              </select>
            </label>
            <label htmlFor="uw-length">
              for
              <select id="uw-length" value={editing.minutes} onChange={(e) => setEditing({ ...editing, minutes: +e.target.value })}>
                {[...new Set([...LENGTHS, editing.minutes])].sort((a, b) => a - b).map((m) => <option key={m} value={m}>{m >= 60 ? `${m / 60} h` : `${m} min`}</option>)}
              </select>
            </label>
            <span className="muted small">{editing.zone}{editing.zone !== zoneName() && <> · <button className="linklike small" onClick={() => setEditing({ ...editing, zone: zoneName() })}>use {zoneName()}</button></>}</span>
            <button className="primary" disabled={editing.days.length === 0} title={editing.days.length ? undefined : "choose at least one day"} onClick={() => save(editing)}>Save</button>
            <button onClick={() => setEditing(null)}>Cancel</button>
          </div>
        )}
        {saveError && <p className="error">{saveError}</p>}
        <p className="faint small">The time zone is named, so the window follows daylight saving. A deploy may still be approved for another window: it only warns.</p>
      </div>
    </section>
  );
}
