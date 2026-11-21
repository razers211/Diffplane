import { useState } from "react";
import { saveSyslog, type Syslog } from "./api";
import { useCan } from "./role";

// Syslog: per fabric, off by default. On, every switch sends warnings and worse
// over MEth to the receiver (the southbound VIP in HA). Only the platform's own loghost is managed;
// hand-made ones stay. Intent: it shows in the pending bar and is deployed as usual.

export function FabricSyslog({ site, syslog, onSaved }: { site: string; syslog: Syslog; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const [editing, setEditing] = useState(false);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setHost(syslog.host ?? "");
    setPort(String(syslog.port ?? 514));
    setError(null);
    setEditing(true);
  };
  const save = async (enabled: boolean) => {
    setError(null);
    try {
      const saved = await saveSyslog(site, { enabled, host: host.trim() || null, port: Number(port) || null });
      setEditing(false);
      onSaved(enabled ? `Syslog on: switches will send to ${saved.host}:${saved.port} at their next deploy.`
        : "Syslog off: switches stop sending at their next deploy.");
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    }
  };

  return (
    <div className="routing">
      {!editing ? (
        <p>
          Syslog:{" "}
          <span className="mono">{syslog.enabled ? `on, to ${syslog.host}:${syslog.port} through _management_vpn_` : "off"}</span>{" "}
          {canOperate && <button onClick={open}>{syslog.enabled ? "Change" : "Turn on"}</button>}
          {canOperate && syslog.enabled && <button onClick={() => save(false)}>Turn off</button>}
        </p>
      ) : (
        <div className="form-row">
          <label htmlFor="sl-host">
            Receiver address
            <input id="sl-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="IPv4, reached over MEth" />
          </label>
          <label htmlFor="sl-port">
            UDP port
            <input id="sl-port" value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" />
          </label>
          <button className="primary" onClick={() => save(true)}>Save and turn on</button>
          <button onClick={() => setEditing(false)}>Cancel</button>
          {error && <p className="error">{error}</p>}
        </div>
      )}
      <p className="faint small">Warnings and worse, from each switch's MEth address; shown on its Logs tab, kept 90 days. Other loghosts on the switches are left alone.</p>
    </div>
  );
}
