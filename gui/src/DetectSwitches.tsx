import { useState } from "react";
import { approveImportKeys, detectImport, startImport, type ImportSession, type ImportSwitch } from "./api";
import { KeyRows } from "./HostKeys";
import { offeredKeys, parseAddresses } from "./importFabric";
import { useCan } from "./role";

// New fabric and Add switches: switches already reachable are detected before they are placed.
// Their host keys are fetched, an approver compares and approves them (as in Import), then the
// platform reads each one's model and front-panel ports over NETCONF. A detected switch is placed
// as one of the new switches: it is built with the ports it reported, whatever its model.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

function Row({ s, session, onSession, names, onUse }: {
  s: ImportSwitch; session: string; onSession: (x: ImportSession) => void;
  names: string[]; onUse: (name: string) => void;
}) {
  const canApprove = useCan("approver");
  const [compared, setCompared] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const approve = async () => {
    setError(null);
    try {
      onSession(await approveImportKeys(session, s.address, offeredKeys(s)));
    } catch (e) {
      setError(clean(e));
    }
  };
  const d = s.detected;
  return (
    <tr>
      <td className="mono">{s.address}</td>
      <td>
        <KeyRows keys={(s.offered["22"] ?? []).concat((s.offered["830"] ?? []).filter((k) => !(s.offered["22"] ?? []).some((x) => x.key === k.key)))} />
        {!s.same_on_both_ports && Object.keys(s.offered).length > 0 && <p className="warning small">Ports 22 and 830 present different keys.</p>}
      </td>
      <td>
        {s.approved ? <span className="pill good">approved by {s.approved_by}</span>
          : canApprove && Object.keys(s.offered).length > 0 ? (
            <span className="inline">
              <label className="check small">
                <input type="checkbox" checked={compared} onChange={(e) => setCompared(e.target.checked)} />
                compared with the console
              </label>
              <button className="small primary" disabled={!compared} onClick={approve}>Approve keys</button>
            </span>
          ) : <span className="muted small">{canApprove ? "" : "an approver compares and approves"}</span>}
        {(error ?? s.error) && <p className="error small">{error ?? s.error}</p>}
      </td>
      <td>
        {d ? (
          <>
            <span className="mono">{d.model}</span>
            <span className="muted small"> · {Object.keys(d.ports).length} ports · {d.version}{d.serial ? ` · ${d.serial}` : ""}</span>
          </>
        ) : <span className="faint">not read</span>}
      </td>
      <td>
        {d && (
          <label className="small">Place as{" "}
            <select value="" onChange={(e) => e.target.value && onUse(e.target.value)} aria-label={`Place ${s.address} as`}>
              <option value="">—</option>
              {names.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
      </td>
    </tr>
  );
}

/** `names`: the new switches of the batch, to place a detected switch as. */
export function DetectSwitches({ session, onSession, names, onUse }: {
  session: ImportSession | null; onSession: (s: ImportSession | null) => void;
  names: string[]; onUse: (name: string, s: ImportSwitch) => void;
}) {
  const [addresses, setAddresses] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const step = async (what: string, call: () => Promise<void>) => {
    setError(null);
    setBusy(what);
    try {
      await call();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="panel detect-switches">
      <header data-tour="detect-switches">
        <h2>Detect switches</h2>
        <span className="hint">reachable switches: their model and ports are read from them</span>
      </header>
      <div className="pad">
      <p className="muted small">
        Give the management addresses of switches that already answer. After an approver has compared their host keys
        with the console, the platform reads each switch's model and front-panel ports over NETCONF (nothing is changed
        on it), so a switch of any model is built with the ports it really has. Place each one as a new switch below;
        its keys are pinned when the switches are written.
      </p>
      {error && <p className="error">{error}</p>}
      </div>
      {!session ? (
        <div className="form-row pad">
          <label>Addresses
            <textarea className="mono" rows={2} value={addresses} onChange={(e) => setAddresses(e.target.value)}
              placeholder={"172.24.0.130/24\n172.24.0.131/24"} />
          </label>
          <button disabled={!parseAddresses(addresses).length || !!busy}
            onClick={() => step("start", async () => onSession(await startImport(parseAddresses(addresses))))}>
            {busy === "start" ? "Fetching keys…" : "Fetch host keys"}
          </button>
        </div>
      ) : (
        <>
          <table className="devices">
            <thead><tr><th>Address</th><th>Host key</th><th>Status</th><th>Detected</th><th /></tr></thead>
            <tbody>
              {session.switches.map((s) => (
                <Row key={s.address} s={s} session={session.id} onSession={onSession} names={names} onUse={(n) => onUse(n, s)} />
              ))}
            </tbody>
          </table>
          <div className="inline pad">
            <button className="primary" disabled={!session.switches.some((s) => s.approved) || !!busy}
              onClick={() => step("detect", async () => onSession(await detectImport(session.id)))}>
              {busy === "detect" ? "Reading…" : "Read model and ports"}
            </button>
            <button onClick={() => onSession(null)}>Start again</button>
          </div>
        </>
      )}
    </div>
  );
}
