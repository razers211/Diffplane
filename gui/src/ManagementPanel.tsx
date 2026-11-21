import { useState } from "react";
import { activateDevice, decommissionDevice, fetchDevices, fetchRemoval, removeDevice } from "./api";
import { HostKeys } from "./HostKeys";
import { ZtpPanel } from "./ZtpPanel";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// A switch's lifecycle: activate it with its management address, change
// that address, or decommission it. The GUI owns status and management addresses; the seed never
// writes them. Decommissioning leaves the switch's config alone. A planned switch can be removed
// from the fabric (a leaf with its M-LAG peer), with its cables and addresses.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function ManagementPanel({ name }: { name: string }) {
  const devices = useFetch(() => fetchDevices(), []);
  const device = devices.data?.find((d) => d.name === name);
  const canOperate = useCan("operator");
  const [editing, setEditing] = useState(false);
  const [address, setAddress] = useState("");
  const [iface, setIface] = useState("MEth0/0/0");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [removal, setRemoval] = useState<{ devices: string[]; links: string[]; unpair: string | null } | null>(null);

  if (!device) return null;
  const active = device.status === "active";

  const open = () => {
    setAddress(device.primary_ip ?? "");
    setError(null);
    setDone(null);
    setEditing(true);
  };
  const activate = async () => {
    setError(null);
    try {
      const r = await activateDevice(name, { address: address.trim(), interface: iface.trim() });
      setEditing(false);
      setDone(r.pinned ? `${name} is active at ${r.address}.`
        : `${name} is active at ${r.address}. Pin its host keys (below) before anything connects to it.`);
      devices.reload();
    } catch (e) {
      setError(clean(e));
    }
  };
  const askRemoval = async () => {
    setError(null);
    setDone(null);
    try {
      setRemoval(await fetchRemoval(name));
    } catch (e) {
      setError(clean(e));
    }
  };
  const remove = async () => {
    setError(null);
    try {
      await removeDevice(name);
      window.location.hash = "#/switches";
    } catch (e) {
      setError(clean(e));
    }
  };
  const decommission = async () => {
    setError(null);
    try {
      const r = await decommissionDevice(name);
      setAsking(false);
      setDone(`${name} is decommissioned (planned)${r.freed.length ? `; freed ${r.freed.join(", ")}` : ""}. ` +
        "Its config on the switch was left alone.");
      devices.reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <div className="panel management" data-tour="management">
      <header>
        <h2>Management</h2>
        <span className={`pill status-${device.status}`}>{device.status}</span>
        {active && <span className={device.pinned ? "pill good" : "pill warnpill"}>{device.pinned ? "keys pinned" : "keys not pinned"}</span>}
        {canOperate && !editing && !asking && (
          <span className="inline actions-right">
            <button onClick={open}>{active ? "Change address" : "Activate"}</button>
            {active && <button onClick={() => setAsking(true)}>Decommission</button>}
            {!active && !removal && <button onClick={askRemoval}>Remove switch</button>}
          </span>
        )}
      </header>
      {!editing && !asking && (
        <p className="empty">
          {active
            ? <>Management address <span className="mono">{device.primary_ip}</span>. Collected, drift-checked and deployable{device.pinned ? "" : " once its host keys are pinned"}.</>
            : "Modelled only: nothing fetches, collects or deploys to it. Activate it once it is cabled and reachable."}
        </p>
      )}
      {editing && (
        <div className="form-row place-form">
          <label>Interface<input className="mono narrow-wide" value={iface} onChange={(e) => setIface(e.target.value)} /></label>
          <label>Address with mask<input className="mono" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="172.24.0.105/24" /></label>
          <div className="inline">
            <button className="primary" onClick={activate} disabled={!address.trim()}>{active ? "Save" : "Activate"}</button>
            <button onClick={() => setEditing(false)}>Cancel</button>
          </div>
          <p className="muted small">The address must already be configured on the switch's MEth port (bring-up or ZTP).</p>
        </div>
      )}
      {asking && (
        <div className="form-row place-form">
          <p>
            Decommission <b>{name}</b>? It goes back to planned: its management address is freed and its host keys
            forgotten, so the collector, drift checks and deploys stop reaching it. Its fabric addresses and its config
            on the switch stay.
          </p>
          <div className="inline">
            <button className="danger" onClick={decommission}>Decommission</button>
            <button onClick={() => setAsking(false)}>Cancel</button>
          </div>
        </div>
      )}
      {removal && (
        <div className="form-row place-form">
          <p>
            Remove <b>{removal.devices.join(" and ")}</b> from the fabric? {removal.devices.length > 1 && "An M-LAG pair is removed whole. "}
            {removal.unpair && <>Its M-LAG peer <b>{removal.unpair}</b> stays, as a single spine: it loses its peer-link, bypass link and VTEP. </>}
            Their ports, cables, loopbacks{removal.devices.length > 1 ? ", VTEP, bypass link" : ""} and point-to-point
            addresses are deleted from NetBox.
            {removal.links.length > 0 && <> The other ends of their links lose their link config at their next deploy:{" "}
              <span className="mono">{removal.links.join(", ")}</span>.</>}
            {" "}A switch that is still in the fabric YAML comes back at the next seed: remove it there too.
          </p>
          <div className="inline">
            <button className="danger" onClick={remove}>Remove</button>
            <button onClick={() => setRemoval(null)}>Cancel</button>
          </div>
        </div>
      )}
      {active && !editing && !asking && (
        <div className="place-form"><HostKeys name={name} pinned={device.pinned} onChanged={devices.reload} /></div>
      )}
      {!active && !editing && !removal && (
        <div className="place-form hostkeys"><ZtpPanel name={name} site={device.site} mac={device.meth_mac} onActivated={devices.reload} /></div>
      )}
      {done && <p className="notice empty">{done}</p>}
      {error && <p className="error empty">{error}</p>}
    </div>
  );
}
