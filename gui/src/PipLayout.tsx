import { useState, type FormEvent } from "react";
import { setPipPool } from "./api";
import { useCan } from "./role";

/** Move a fabric's M-LAG pairs to the PIP layout, so their heartbeat leaves the peer-link. */
export function PipLayout({ site, onSaved }: { site: string; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const [value, setValue] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!canOperate) return null;
  const ask = (e: FormEvent) => { e.preventDefault(); setError(null); setConfirming(true); };
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await setPipPool(site, value ?? "");
      setValue(null);
      setConfirming(false);
      onSaved(`PIP layout set (${saved.prefix}) on ${saved.switches.join(", ")}. Review and deploy the change.`);
    } catch (err) {
      setError(err instanceof Error ? err.message.replace(/^\d+: /, "") : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="pad pip-layout">
      {value === null ? (
        <p>
          The M-LAG heartbeat of these pairs follows the bypass route over the peer-link.{" "}
          <button onClick={() => setValue("")}>Move pairs to the PIP layout</button>
        </p>
      ) : (
        <form className="form-row" onSubmit={ask}>
          <label htmlFor="pip-pool">PIP pool
            <input id="pip-pool" className="mono" value={value} onChange={(e) => { setValue(e.target.value); setConfirming(false); }}
              placeholder="e.g. 10.0.4.0/24" />
          </label>
          <button type="submit" className="primary" disabled={!value.trim() || confirming}>Continue</button>
          <button type="button" onClick={() => { setValue(null); setConfirming(false); }}>Cancel</button>
        </form>
      )}
      {confirming && (
        <div className="confirm-inline" role="alertdialog" aria-label="Confirm">
          <span>
            Each M-LAG switch gets its own PIP on LoopBack1 from {value}, and the pair's VTEP address moves to LoopBack2.
            The next deploy re-addresses those loopbacks and the NVE: VXLAN tunnels flap while it runs.
          </span>
          <button className="danger" disabled={busy} onClick={save}>Yes, move them</button>
          <button onClick={() => setConfirming(false)}>Not now</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <p className="faint small">A /32 per M-LAG switch, in the fabric's address namespace. Turn DHCP relay off first if a pair relays.</p>
    </div>
  );
}
