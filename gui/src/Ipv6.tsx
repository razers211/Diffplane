import { useState, type FormEvent } from "react";
import { setIpv6Pool, setSegmentIpv6, type Ipv6Hosts, type RelayServer, type Segment, type Service } from "./api";
import { MAX_SERVERS, relayPlaces } from "./Relay";
import { useCan } from "./role";

// IPv6 of routed segments: a /64 per segment from the fabric's IPv6
// pool, gateway ::1, hosts on SLAAC (default), DHCPv6 relayed through the segment's relay loopback,
// or none.

export const HOSTS: { value: Ipv6Hosts; label: string; hint: string }[] = [
  { value: "slaac", label: "SLAAC", hint: "router advertisements: hosts make their own addresses" },
  { value: "dhcpv6", label: "DHCPv6", hint: "advertisements tell hosts to ask a DHCPv6 server, relayed here" },
  { value: "none", label: "none", hint: "no advertisements: addresses set by hand" },
];

/** Where a DHCPv6 server may sit: as for DHCP, but only in segments, and behind a firewall,
 *  that have IPv6 too. */
export function ipv6Places(segment: Segment, segments: Segment[], centralized: boolean, services: Service[] = []) {
  return relayPlaces(segment, segments, centralized).filter((p) => {
    if (p.value.startsWith("service:")) return !!services.find((s) => s.name === p.value.slice(8))?.ipv6;
    if (p.value.startsWith("segment:")) return !!segments.find((s) => s.vni === Number(p.value.slice(8)))?.routing?.ipv6;
    return true;
  });
}

const placeOf = (s: RelayServer) => (s.service ? `service:${s.service}` : s.segment !== undefined ? `segment:${s.segment}` : "");
const serverOf = (address: string, place: string): RelayServer =>
  place.startsWith("segment:") ? { address, segment: Number(place.slice(8)) }
    : place.startsWith("service:") ? { address, service: place.slice(8) } : { address };
const problem = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));

export function SegmentIpv6({ segment, segments, services, centralized, pool, onDone }: {
  segment: Segment; segments: Segment[]; services: Service[]; centralized: boolean; pool: string | null;
  onDone: (text: string) => void;
}) {
  const canOperate = useCan("operator");
  const six = segment.routing?.ipv6 ?? null;
  const [hosts, setHosts] = useState<Ipv6Hosts | null>(null);
  const [rows, setRows] = useState<{ address: string; place: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  if (!segment.fabric || !segment.routing) return null;
  const places = ipv6Places(segment, segments, centralized, services);

  const edit = () => {
    setHosts(six?.hosts ?? "slaac");
    setRows(six?.servers.map((s) => ({ address: s.address, place: placeOf(s) })) ?? []);
    setError(null);
  };
  const save = async (body: { enabled: boolean; hosts?: Ipv6Hosts; servers?: RelayServer[] }, text: string) => {
    setError(null);
    try {
      await setSegmentIpv6(segment.fabric!, segment.vni, body);
      setHosts(null);
      onDone(text);
    } catch (e) {
      setError(problem(e));
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const servers = hosts === "dhcpv6" ? rows.filter((r) => r.address.trim()).map((r) => serverOf(r.address.trim(), r.place)) : [];
    void save({ enabled: true, hosts: hosts!, servers }, `IPv6 of ${segment.name} saved.`);
  };

  return (
    <div className="routing ipv6" data-tour="ipv6">
      {hosts === null && (
        <p>
          IPv6:{" "}
          {six ? (
            <>
              gateway <span className="mono">{six.gateway}</span> · hosts {HOSTS.find((h) => h.value === six.hosts)?.label}
              {six.servers.length > 0 && <> · DHCPv6 to <span className="mono">{six.servers.map((s) => s.address).join(", ")}</span></>}
            </>
          ) : <span className="muted">off (IPv4 only)</span>}{" "}
          {canOperate && <button onClick={edit} disabled={!pool} title={pool ? "" : "Set the fabric's IPv6 pool first (Fabric page)"}>
            {six ? "Change" : "Turn on"}</button>}
          {canOperate && six && <button onClick={() => save({ enabled: false }, `${segment.name} is IPv4 only again.`)}>Turn off</button>}
        </p>
      )}
      {hosts !== null && (
        <form onSubmit={submit} className="relay-form">
          <div className="chips" role="group" aria-label="How hosts get IPv6 addresses">
            {HOSTS.map((h) => (
              <button type="button" key={h.value} className="chip" aria-pressed={hosts === h.value} title={h.hint}
                onClick={() => setHosts(h.value)}>{h.label}</button>
            ))}
          </div>
          <p className="faint small">{HOSTS.find((h) => h.value === hosts)?.hint}</p>
          {hosts === "dhcpv6" && rows.map((row, i) => (
            <div key={i} className="form-row">
              <label htmlFor={`v6-${segment.vni}-${i}`}>DHCPv6 server
                <input id={`v6-${segment.vni}-${i}`} value={row.address} placeholder="IPv6 address"
                       onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, address: e.target.value } : r)))} />
              </label>
              <label>Where
                <select value={row.place} onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, place: e.target.value } : r)))}>
                  {places.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
            </div>
          ))}
          <div className="actions">
            {hosts === "dhcpv6" && rows.length < MAX_SERVERS &&
              <button type="button" onClick={() => setRows([...rows, { address: "", place: "" }])}>Add a DHCPv6 server</button>}
            <button type="submit" className="primary">Save</button>
            <button type="button" onClick={() => setHosts(null)}>Cancel</button>
          </div>
        </form>
      )}
      {!pool && hosts === null && canOperate && <p className="faint small">Needs the fabric's IPv6 pool, set on the Fabric page.</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** The fabric's IPv6 pool (Fabric page): its first /64 for relay loopbacks, a /64 per segment. */
export function FabricIpv6Pool({ site, pool, onSaved }: { site: string; pool: string | null; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const [value, setValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const saved = await setIpv6Pool(site, value ?? "");
      setValue(null);
      onSaved(`IPv6 pool ${saved.prefix} set; ${saved.relay} is kept for relay loopbacks.`);
    } catch (err) {
      setError(problem(err));
    }
  };
  return (
    <div className="routing">
      {value === null ? (
        <p>
          IPv6 pool: <span className="mono">{pool ?? "not set (segments stay IPv4 only)"}</span>{" "}
          {canOperate && <button onClick={() => setValue(pool ?? "")}>{pool ? "Change" : "Set"}</button>}
        </p>
      ) : (
        <form className="form-row" onSubmit={save}>
          <label htmlFor="ipv6-pool">IPv6 pool
            <input id="ipv6-pool" value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. fd12:3456:789a::/48" />
          </label>
          <button type="submit" className="primary">Save</button>
          <button type="button" onClick={() => setValue(null)}>Cancel</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
      <p className="faint small">ULA (fd00::/8) or provider space, a /32 to a /56. Its first /64 holds relay loopbacks; each segment gets the next free /64.</p>
    </div>
  );
}
