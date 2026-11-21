import { useState } from "react";
import { fetchSwitchHardware, readSwitch, type SwitchReadResult } from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// What the switch is, as it reports it: model, software release and patch. The platform reads
// it when the switch is pinned and every hour; "Read from the switch" does it now. Its ports, with
// their speed and copper or fibre, then follow in NetBox, unless a port in use would disappear.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function outcome(r: SwitchReadResult): string {
  if (r.blocked.length) {
    return `Nothing changed: the switch has no ${r.blocked.join(", ")}, which ${r.blocked.length > 1 ? "are" : "is"} in use here. `
      + "Move what uses them, then read again.";
  }
  if (!r.changed) return "Everything already matches the switch.";
  const parts = [];
  if (r.was_model !== r.model) parts.push(`model ${r.was_model ?? "none"} → ${r.model}`);
  if (r.added.length) parts.push(`${r.added.length} port${r.added.length > 1 ? "s" : ""} added`);
  if (r.retyped.length) parts.push(`${r.retyped.length} port type${r.retyped.length > 1 ? "s" : ""} corrected`);
  if (r.removed.length) parts.push(`${r.removed.length} port${r.removed.length > 1 ? "s" : ""} removed`);
  return `Updated: ${parts.join(", ") || "serial number"}.`;
}

export function SwitchHardwarePanel({ name, active, onChanged }: { name: string; active: boolean; onChanged?: () => void }) {
  const info = useFetch(() => fetchSwitchHardware(name), [name]);
  const canOperate = useCan("operator");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const read = async () => {
    setBusy(true); setError(null); setMessage(null);
    try {
      setMessage(outcome(await readSwitch(name)));
      info.reload();
      onChanged?.();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  const d = info.data;
  const differs = d?.reported && d.model && d.reported.model !== d.model;
  return (
    <div className="panel" data-tour="hardware-read">
      <header>
        <h2>Model and software</h2>
        <span className="hint">as the switch reports it</span>
        {canOperate && active && <button onClick={read} disabled={busy}>{busy ? "Reading…" : "Read from the switch"}</button>}
      </header>
      {d && (
        <dl className="settings">
          <dt>Model</dt><dd className="mono">{d.reported?.model ?? d.model ?? "unknown"}</dd>
          <dt>Software</dt><dd className="mono">{d.reported?.version || (active ? "not read yet" : "read once the switch is active")}</dd>
          <dt>Patch</dt><dd className="mono">{d.reported?.patch || "none"}</dd>
          {d.serial && <><dt>Serial</dt><dd className="mono">{d.serial}</dd></>}
        </dl>
      )}
      {differs && (
        <p className="error empty">
          The switch says it is a {d!.reported!.model}, but the platform has it as a {d!.model}. Deploys to it are
          blocked until they match: read it from the switch.
        </p>
      )}
      {message && <p className="empty">{message}</p>}
      {error && <p className="error empty">{error}</p>}
      {info.error && <p className="error empty">{info.error}</p>}
    </div>
  );
}
