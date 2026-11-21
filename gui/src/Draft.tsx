import { useEffect, useState } from "react";
import { createChangeSet, deployChangeSet, discardChangeSet, fetchChangeSet, fetchChangeSets } from "./api";
import { currentDraft, DRAFT_CHANGED, DRAFT_SAVED, setDraft, stepText, type ChangeSet, type Draft } from "./draft";
import { useCan } from "./role";
import { useFetch } from "./useFetch";
import { DEPLOYS_CHANGED } from "./changes";

// Draft mode: changes prepared for a change window. A coloured bar on every
// page while it is on; every page then shows the fabric as the draft would make it.

const errorText = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
const changes = (n: number) => `${n} change${n === 1 ? "" : "s"}`;

/** The draft this tab is in, kept current. */
export function useDraft(): Draft | null {
  const [draft, set] = useState(currentDraft);
  useEffect(() => {
    const on = () => set(currentDraft());
    window.addEventListener(DRAFT_CHANGED, on);
    return () => window.removeEventListener(DRAFT_CHANGED, on);
  }, []);
  return draft;
}

export function DraftBar({ draft }: { draft: Draft }) {
  const [saved, setSaved] = useState(0);
  const { data } = useFetch(() => fetchChangeSet(draft.site, draft.id), [draft.id, saved]);
  useEffect(() => {
    const on = () => setSaved((n) => n + 1);
    window.addEventListener(DRAFT_SAVED, on);
    return () => window.removeEventListener(DRAFT_SAVED, on);
  }, []);
  useEffect(() => {
    if (data && data.status !== "open") setDraft(null);   // deployed, applied or discarded meanwhile
  }, [data]);
  return (
    <div className="draft-bar" role="status">
      <span className="grow">
        <b>Draft #{draft.id} for a window</b> ({draft.site}): {changes(data?.steps.length ?? 0)}. You see the fabric as it
        would be; saves go into the draft, not to the switches or the live intent.
      </span>
      <a className="button" href="#/changes">Review</a>
      <button onClick={() => setDraft(null)}>Back to live</button>
    </div>
  );
}

/** Changes › Pending: drafts of this fabric, to open, deploy for a window or discard. */
export function ChangeSetsPanel({ site }: { site: string }) {
  const canOperate = useCan("operator");
  const draft = useDraft();
  const { data, error, reload } = useFetch(() => fetchChangeSets(site), [site, draft?.id]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState<number | null>(null);
  const open = (data ?? []).filter((c) => c.status === "open");

  const act = async (what: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await what();
      reload();
    } catch (e) {
      setProblem(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const start = () => act(async () => setDraft({ site, id: (await createChangeSet(site)).id }));
  const deploy = (c: ChangeSet) => act(async () => {
    const { job_id } = await deployChangeSet(site, c.id);
    if (draft?.id === c.id) setDraft(null);
    window.dispatchEvent(new Event(DEPLOYS_CHANGED));
    window.location.hash = `#/changes/deploys/${job_id}`;
  });
  const discard = (c: ChangeSet) => act(async () => {
    await discardChangeSet(site, c.id);
    if (draft?.id === c.id) setDraft(null);
    setDiscarding(null);
  });

  if (!site || (!canOperate && open.length === 0)) return null;
  return (
    <section className="panel change-sets" aria-label="Changes for a window" data-tour="change-sets">
      <header>
        <h2>Changes for a window</h2>
        <span className="hint">saved now, applied and deployed at the window's start</span>
      </header>
      <div className="pad">
        {error && <p className="error">{error}</p>}
        {open.length === 0 && (
          <p className="empty">
            No draft. Prepare changes for a window: until you go back to live, every save goes into a draft that changes
            nothing now. Others keep working and deploying meanwhile.
          </p>
        )}
        {open.map((c) => (
          <div key={c.id} className="change-set">
            <div className="change-set-head">
              <b>Draft #{c.id}</b>
              <span className="muted small">by {c.author} · {changes(c.steps.length)}</span>
              {draft?.id === c.id && <span className="pill info">open in this tab</span>}
              {canOperate && draft?.id !== c.id && <button className="small" onClick={() => setDraft({ site, id: c.id })}>Open</button>}
              {canOperate && (
                <button className="small primary" disabled={busy || c.steps.length === 0}
                  title={c.steps.length ? undefined : "nothing saved in it yet"} onClick={() => deploy(c)}>Prepare its deploy</button>
              )}
              {canOperate && discarding !== c.id && <button className="small" onClick={() => setDiscarding(c.id)}>Discard</button>}
            </div>
            {discarding === c.id && (
              <div className="confirm-inline" role="alertdialog" aria-label="Confirm">
                <span>Discard draft #{c.id}? Its {changes(c.steps.length)} are dropped; nothing was ever applied.</span>
                <button className="danger" disabled={busy} onClick={() => discard(c)}>Yes, discard</button>
                <button onClick={() => setDiscarding(null)}>Keep it</button>
              </div>
            )}
            {c.steps.length > 0 && (
              <ol className="changelist">
                {c.steps.map((s, i) => <li key={i}><b>{stepText(s)}</b><span className="sub">{s.user} · {new Date(s.at).toLocaleString()}</span></li>)}
              </ol>
            )}
          </div>
        ))}
        {canOperate && !draft && <button onClick={start} disabled={busy}>Prepare changes for a window</button>}
        {problem && <p className="error">{problem}</p>}
        <p className="faint small">
          Preparing its deploy computes each switch's patch as the draft would make it; it is approved for a window only.
          At the start the changes are applied, every patch is computed again and must match.
        </p>
      </div>
    </section>
  );
}

/** After a save: how it reaches the switches (a deploy now, or the draft's window). */
export function ApplyHint() {
  const draft = useDraft();
  return draft
    ? <>It is in draft #{draft.id}, applied at its window's start.</>
    : <><a href="#/changes">Review and deploy</a> to apply it.</>;
}
