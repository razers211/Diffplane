import { useState } from "react";
import { fetchHostKeys, forgetHostKeys, pinHostKeys, type HostKeyOffer, type KeyInfo } from "./api";
import { useCan } from "./role";

// Pin a switch's SSH host keys from the GUI, the same steps as
// `python -m orchestrator.hostkeys pin`: fetch what the switch offers on ports 22 and 830, a
// person compares the fingerprints with the switch's own display, an approver pins exactly those.
// The orchestrator fetches again and refuses if the switch now offers anything else.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");
const PORTS: ["22" | "830", string][] = [["22", "SSH (Annet)"], ["830", "NETCONF (collector)"]];

export function KeyRows({ keys }: { keys: KeyInfo[] }) {
  if (keys.length === 0) return <p className="faint small">none</p>;
  return (
    <table className="devices keys">
      <tbody>
        {keys.map((k) => (
          <tr key={k.key}>
            <td className="mono small">{k.type}</td>
            <td className="mono small">{k.sha256}<br /><span className="faint">MD5 {k.md5}</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function HostKeys({ name, pinned, onChanged }: { name: string; pinned: boolean; onChanged: () => void }) {
  const canOperate = useCan("operator");
  const canApprove = useCan("approver");
  const [offer, setOffer] = useState<HostKeyOffer | null>(null);
  const [busy, setBusy] = useState(false);
  const [compared, setCompared] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState(false);

  const fetchNow = async () => {
    setError(null);
    setDone(null);
    setCompared(false);
    setBusy(true);
    try {
      setOffer(await fetchHostKeys(name));
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };
  const pin = async () => {
    if (!offer) return;
    setError(null);
    setBusy(true);
    try {
      const keys = [...new Set([...offer.offered["22"], ...offer.offered["830"]].map((k) => k.key))];
      const r = await pinHostKeys(name, keys);
      setOffer(null);
      setDone(`Pinned ${r.fingerprints.join(", ")}.`);
      onChanged();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };
  const forget = async () => {
    setError(null);
    try {
      await forgetHostKeys(name);
      setForgetting(false);
      setDone("Pins forgotten: Annet and the collector refuse this switch until it is pinned again.");
      onChanged();
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <div className="hostkeys">
      <div className="inline">
        <h3>SSH host keys</h3>
        {canOperate && <button className="small" onClick={fetchNow} disabled={busy}>{busy && !offer ? "Fetching…" : "Fetch keys"}</button>}
        {canApprove && pinned && !forgetting && <button className="small" onClick={() => setForgetting(true)}>Forget pins</button>}
      </div>
      {forgetting && (
        <p>
          Forget the pins? Diffs, deploys and the collector stop reaching {name} until it is pinned again.{" "}
          <button className="danger small" onClick={forget}>Forget</button> <button className="small" onClick={() => setForgetting(false)}>Cancel</button>
        </p>
      )}
      {offer && (
        <div className="offer">
          <p className="muted small">
            {name} ({offer.address}) offers these keys. Fetching over the network proves nothing by itself:
            compare the fingerprints with the switch's own display of its host key on its console
            (<code>display rsa local-key-pair public</code>, operations guide, "SSH host keys") before pinning.
          </p>
          <div className="key-ports">
            {PORTS.map(([port, label]) => (
              <div key={port}>
                <b className="small">Port {port} · {label}</b>
                <KeyRows keys={offer.offered[port]} />
              </div>
            ))}
          </div>
          {!offer.same_on_both_ports && <p className="warning small">Ports 22 and 830 present different keys.</p>}
          {Object.keys(offer.unsupported).length > 0 && (
            <p className="warning small">Not readable, so not pinned: {Object.entries(offer.unsupported).map(([p, a]) => `${p}: ${a.join(", ")}`).join("; ")}</p>
          )}
          {offer.unchanged ? (
            <p className="notice small">These are exactly the keys already pinned: nothing to do.</p>
          ) : (
            <>
              {(offer.pinned["22"].length > 0 || offer.pinned["830"].length > 0) && (
                <div>
                  <p className="warning small">Different from the keys pinned now (a replaced, reset or re-keyed switch, or not this switch):</p>
                  <KeyRows keys={[...offer.pinned["22"], ...offer.pinned["830"].filter((k) => !offer.pinned["22"].some((x) => x.key === k.key))]} />
                </div>
              )}
              {canApprove ? (
                <div className="inline">
                  <label className="check small">
                    <input type="checkbox" checked={compared} onChange={(e) => setCompared(e.target.checked)} />
                    I compared these fingerprints with the switch's console
                  </label>
                  <button className="primary small" onClick={pin} disabled={!compared || busy}>{busy ? "Pinning…" : "Pin these keys"}</button>
                  <button className="small" onClick={() => setOffer(null)}>Cancel</button>
                </div>
              ) : (
                <p className="muted small">Pinning needs the approver role.</p>
              )}
            </>
          )}
        </div>
      )}
      {done && <p className="notice small">{done}</p>}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
