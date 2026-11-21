import { useState } from "react";
import { saveNtp, type Ntp } from "./api";
import { useCan } from "./role";

// A fabric's NTP servers: set here once the fabric exists; the seed
// never overwrites them. No servers: the platform manages no NTP lines on the fabric.

export function FabricNtp({ site, ntp, onSaved }: { site: string; ntp: Ntp; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const [editing, setEditing] = useState(false);
  const [servers, setServers] = useState("");
  const [vpn, setVpn] = useState("");
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setServers(ntp.servers.join(", "));
    setVpn(ntp.vpn_instance ?? "");
    setError(null);
    setEditing(true);
  };
  const save = async () => {
    setError(null);
    try {
      const list = servers.split(/[\s,]+/).filter(Boolean);
      await saveNtp(site, { servers: list, vpn_instance: vpn.trim() || null });
      setEditing(false);
      onSaved(list.length ? `NTP servers set: ${list.join(", ")}.` : "NTP is no longer managed on this fabric.");
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    }
  };

  const summary = ntp.servers.length
    ? `${ntp.servers.join(", ")}${ntp.vpn_instance ? ` through ${ntp.vpn_instance}` : ""}`
    : "none (not managed)";
  return (
    <div className="routing">
      {!editing ? (
        <p>
          NTP servers: <span className="mono">{summary}</span>{" "}
          {canOperate && <button onClick={open}>Change</button>}
        </p>
      ) : (
        <div className="form-row">
          <label>
            NTP servers
            <input value={servers} onChange={(e) => setServers(e.target.value)} placeholder="empty = not managed" />
          </label>
          <label>
            Reached through VPN instance
            <input value={vpn} onChange={(e) => setVpn(e.target.value)} placeholder="global table" />
          </label>
          <button className="primary" onClick={save}>
            Save
          </button>
          <button onClick={() => setEditing(false)}>Cancel</button>
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </div>
  );
}
