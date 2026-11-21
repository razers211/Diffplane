import { useState } from "react";
import { fetchOverrides, makeStandard, previewOverride, type OverrideEntry } from "./api";
import { PatchCard } from "./Deploys";
import { changedSwitches, grouped, whereItIs } from "./overrides";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Found on import: what an imported fabric keeps the way it was found. Shown only for an import;
// "Make it standard" shows its patch first, saving is an intent write, a deploy applies it.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

function Row({ site, entry, expert, onSaved }: {
  site: string; entry: OverrideEntry; expert: boolean; onSaved: (text: string) => void;
}) {
  const canApprove = useCan("approver");
  const [patches, setPatches] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  }
  const changed = patches ? changedSwitches(patches) : [];
  return (
    <li>
      <span><b>{whereItIs(entry)}:</b> {entry.text} <span className="hint">Usual form: {entry.derived}.</span></span>
      {expert && <code className="small"> {entry.path} = {JSON.stringify(entry.value)}</code>}
      {canApprove && entry.standard && !patches && (
        <button className="linklike small" disabled={busy}
                onClick={() => run(async () => setPatches((await previewOverride(site, entry.path)).patches))}>
          {busy ? "Working it out…" : "Make it standard"}
        </button>
      )}
      {!entry.standard && <span className="hint"> Stays as it is.</span>}
      {error && <p className="error small">{error}</p>}
      {patches && (
        <div className="pad">
          <p className="small">
            {changed.length ? `This changes ${changed.length} switch(es) at the next deploy:`
              : "No switch changes: the value only leaves this list."}
          </p>
          {changed.map(([device, patch]) => (
            <PatchCard key={device} open={changed.length <= 2}
                       step={{ device, patch, status: "diffed", error: null, started_at: null, finished_at: null }} />
          ))}
          <button className="primary" disabled={busy} onClick={() => run(async () => {
            await makeStandard(site, entry.path);
            onSaved(`${whereItIs(entry)} now takes the usual form. Deploy to apply it.`);
          })}>Save</button>{" "}
          <button className="linklike small" onClick={() => setPatches(null)}>Keep it as found</button>
        </div>
      )}
    </li>
  );
}

export function FabricOverridesPanel({ site, onSaved }: { site: string; onSaved: (text: string) => void }) {
  const found = useFetch(() => fetchOverrides(site), [site]);
  const [expert, setExpert] = useState(false);
  const data = found.data;
  if (!data?.imported) return null;
  const misfits = Object.entries(data.misfits).filter(([, list]) => list.length > 0);
  const saved = (text: string) => { found.reload(); onSaved(text); };
  return (
    <section className="panel ntp-panel" aria-label="Found on import">
      <header>
        <h2>Found on import</h2>
        <button className="linklike small" aria-expanded={expert} onClick={() => setExpert(!expert)}>
          {expert ? "Simple view" : "Expert view"}
        </button>
      </header>
      <div className="pad">
        <p>This fabric keeps {data.entries.length} things the way they were found.</p>
        {grouped(data.entries).map(([area, entries]) => (
          <details key={area}>
            <summary>{area} ({entries.length})</summary>
            <ul>{entries.map((e) => <Row key={e.path} site={site} entry={e} expert={expert} onSaved={saved} />)}</ul>
          </details>
        ))}
        {misfits.length > 0 && (
          <details>
            <summary>Not managed ({misfits.reduce((n, [, list]) => n + list.length, 0)})</summary>
            <ul>{misfits.flatMap(([device, list]) => list.map((text) => <li key={device + text}><b>{device}:</b> {text}</li>))}</ul>
          </details>
        )}
      </div>
    </section>
  );
}
