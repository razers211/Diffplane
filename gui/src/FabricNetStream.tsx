import { useState } from "react";
import { saveNetStream, type NetStream } from "./api";
import { useCan } from "./role";

// Sampled NetStream: per fabric, off by default. On, every switch exports
// 1-in-1024 samples of edge ports (IPv4) and leaf uplinks (VXLAN inner) over MEth to the collector.
// Intent: it shows in the pending bar and is deployed as usual.

export function FabricNetStream({ site, netstream, onSaved }: { site: string; netstream: NetStream; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const [editing, setEditing] = useState(false);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setHost(netstream.host ?? "");
    setPort(String(netstream.port ?? 2055));
    setError(null);
    setEditing(true);
  };
  const save = async (enabled: boolean) => {
    setError(null);
    try {
      const saved = await saveNetStream(site, { enabled, host: host.trim() || null, port: Number(port) || null });
      setEditing(false);
      onSaved(enabled ? `NetStream on: switches will export to ${saved.host}:${saved.port} at their next deploy.`
        : "NetStream off: switches stop exporting at their next deploy.");
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    }
  };

  return (
    <div className="routing">
      {!editing ? (
        <p>
          NetStream:{" "}
          <span className="mono">{netstream.enabled ? `on, to ${netstream.host}:${netstream.port} through _management_vpn_` : "off"}</span>{" "}
          {canOperate && <button onClick={open}>{netstream.enabled ? "Change" : "Turn on"}</button>}
          {canOperate && netstream.enabled && <button onClick={() => save(false)}>Turn off</button>}
        </p>
      ) : (
        <div className="form-row">
          <label htmlFor="ns-host">
            Collector address
            <input id="ns-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="IPv4, reached over MEth" />
          </label>
          <label htmlFor="ns-port">
            UDP port
            <input id="ns-port" value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" />
          </label>
          <button className="primary" onClick={() => save(true)}>Save and turn on</button>
          <button onClick={() => setEditing(false)}>Cancel</button>
          {error && <p className="error">{error}</p>}
        </div>
      )}
      <p className="faint small">Edge ports and leaf uplinks sample 1 packet in 1024; never MEth. Off by default.</p>
    </div>
  );
}
