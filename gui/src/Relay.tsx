import { useState, type FormEvent } from "react";
import { setRelay, setRelayPool, type RelayServer, type Segment } from "./api";
import { useCan } from "./role";

// DHCP relay of a routed segment: up to 8 servers. Every gateway leaf relays
// from its own loopback (from the fabric's relay pool) with Option 82 link-selection, so the server
// picks the segment's pool by subnet. The server must honour link-selection and server-id-override.

export const MAX_SERVERS = 8;

/** Where a server may sit, as the API allows it: the segment's own VPN instance, behind its own
 *  firewall, or in a peered segment (symmetric fabrics only: centralized peering routes subnets only). */
export function relayPlaces(segment: Segment, segments: Segment[], centralized: boolean): { value: string; label: string }[] {
  const places = [{ value: "", label: `in ${segment.name} (its own VPN instance)` }];
  if (segment.routing?.firewall) places.push({ value: `service:${segment.routing.firewall}`, label: `behind firewall ${segment.routing.firewall}` });
  if (!centralized) {
    for (const vni of segment.routing?.peers ?? []) {
      const peer = segments.find((s) => s.vni === vni);
      if (peer) places.push({ value: `segment:${vni}`, label: `in peered segment ${peer.name}` });
    }
  }
  return places;
}

const placeOf = (s: RelayServer) => (s.service ? `service:${s.service}` : s.segment !== undefined ? `segment:${s.segment}` : "");

function serverOf(address: string, place: string): RelayServer {
  const [kind, value] = place.split(":");
  if (kind === "service") return { address, service: value };
  if (kind === "segment") return { address, segment: Number(value) };
  return { address };
}

export function SegmentRelay({ segment, segments, centralized, relayPool, onDone }: {
  segment: Segment; segments: Segment[]; centralized: boolean; relayPool: string | null; onDone: (text: string) => void;
}) {
  const canOperate = useCan("operator");
  const relay = segment.routing?.relay ?? null;
  const [rows, setRows] = useState<{ address: string; place: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!segment.fabric || !segment.routing) return null;
  const places = relayPlaces(segment, segments, centralized);
  const label = (s: RelayServer) => places.find((p) => p.value === placeOf(s))?.label.replace(/ \(.*\)$/, "") ?? "";

  const edit = () => {
    setRows(relay?.servers.map((s) => ({ address: s.address, place: placeOf(s) })) ?? [{ address: "", place: "" }]);
    setError(null);
  };
  const save = async (servers: RelayServer[]) => {
    setError(null);
    try {
      await setRelay(segment.fabric!, segment.vni, servers);
      setRows(null);
      onDone(servers.length ? `DHCP relay of ${segment.name} saved.` : `DHCP relay of ${segment.name} is off.`);
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void save((rows ?? []).filter((r) => r.address.trim()).map((r) => serverOf(r.address.trim(), r.place)));
  };

  return (
    <div className="routing relay" data-tour="relay">
      {!rows && (
        <p>
          DHCP relay:{" "}
          {relay ? (
            <>
              {relay.servers.map((s) => <span key={s.address} className="mono">{s.address} </span>)}
              <span className="muted">({relay.servers.map(label).join("; ")}) · from LoopBack{relay.loopback} on each gateway leaf</span>
            </>
          ) : <span className="muted">off</span>}{" "}
          {canOperate && <button onClick={edit} disabled={!relayPool} title={relayPool ? "" : "Set the fabric's relay pool first (Fabric page)"}>
            {relay ? "Change" : "Turn on"}</button>}
          {canOperate && relay && <button onClick={() => save([])}>Turn off</button>}
        </p>
      )}
      {!relayPool && !rows && canOperate && <p className="faint small">Needs the fabric's relay loopback pool, set on the Fabric page.</p>}
      {rows && (
        <form onSubmit={submit} className="relay-form">
          {rows.map((row, i) => (
            <div key={i} className="form-row">
              <label htmlFor={`relay-${segment.vni}-${i}`}>DHCP server
                <input id={`relay-${segment.vni}-${i}`} value={row.address} placeholder="IPv4 address"
                       onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, address: e.target.value } : r)))} />
              </label>
              <label>Where
                <select value={row.place} onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, place: e.target.value } : r)))}>
                  {places.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label="Remove this server">Remove</button>
            </div>
          ))}
          <div className="actions">
            {rows.length < MAX_SERVERS && <button type="button" onClick={() => setRows([...rows, { address: "", place: "" }])}>Add a server</button>}
            <button type="submit" className="primary">Save</button>
            <button type="button" onClick={() => setRows(null)}>Cancel</button>
          </div>
          <p className="faint small">The DHCP server must support Option 82 link-selection (RFC 3527) and server-id-override (RFC 5107).</p>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** The fabric's relay loopback pool (Fabric page): each gateway leaf takes a /32 from it per
 *  relaying segment; the DHCP servers' networks must route it back to the leaves. */
export function FabricRelayPool({ site, pool, onSaved }: { site: string; pool: string | null; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const [value, setValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const saved = await setRelayPool(site, value ?? "");
      setValue(null);
      onSaved(`Relay loopback pool ${saved.prefix} set.`);
    } catch (err) {
      setError(err instanceof Error ? err.message.replace(/^\d+: /, "") : String(err));
    }
  };
  return (
    <div className="routing">
      {value === null ? (
        <p>
          Relay loopback pool: <span className="mono">{pool ?? "not set"}</span>{" "}
          {canOperate && <button onClick={() => setValue(pool ?? "")}>{pool ? "Change" : "Set"}</button>}
        </p>
      ) : (
        <form className="form-row" onSubmit={save}>
          <label htmlFor="relay-pool">Relay loopback pool
            <input id="relay-pool" value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. 10.10.8.0/22" />
          </label>
          <button type="submit" className="primary">Save</button>
          <button type="button" onClick={() => setValue(null)}>Cancel</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
      <p className="faint small">One /32 per gateway leaf and relaying segment, in the fabric's address namespace. It cannot change while segments relay.</p>
    </div>
  );
}
