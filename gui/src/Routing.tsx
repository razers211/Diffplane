import { Faceplate } from "./Faceplate";
import { refusal, trunkOrPort } from "./frontPanel";
import { useEffect, useState, type FormEvent } from "react";
import {
  createService,
  deleteService,
  setServiceAsn,
  setServiceIpv6,
  fetchArchitecture,
  fetchDevices,
  fetchPorts,
  fetchRoutingTargets,
  L3_VNI_OFFSET,
  setRouting,
  type Port,
  type Segment,
  type Service,
} from "./api";
import { serviceLeaves } from "./placement";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

function message(e: unknown): string {
  return e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e);
}

const EMPTY: Service = { name: "", device: "", port: "", vlan: 0, address: "", next_hop: "", asn: null, kind: "firewall" };

function parseAsn(text: string): { ok: true; asn: number | null } | { ok: false; error: string } {
  if (!text.trim()) return { ok: true, asn: null };
  const asn = Number(text);
  return Number.isInteger(asn) && asn >= 1 && asn <= 4294967295
    ? { ok: true, asn }
    : { ok: false, error: "ASN must be a number from 1 to 4294967295 (empty = no BGP)" };
}

export function Services({
  site,
  services,
  segments = [],
  onDone,
}: {
  site: string;
  services: Service[];
  segments?: Segment[];
  onDone: (t: string) => void;
}) {
  const canOperate = useCan("operator");
  const devices = useFetch(() => fetchDevices(site), [site]);
  const arch = useFetch(() => fetchArchitecture(site), [site]);
  const leaves = serviceLeaves(devices.data ?? [], arch.data?.gateway_pair ?? null);
  const [form, setForm] = useState<Service>(EMPTY);
  const [vlan, setVlan] = useState("");
  const [asn, setAsn] = useState("");
  const [ports, setPorts] = useState<Port[]>([]);
  const [error, setError] = useState<string | null>(null);
  // optional IPv6, a subnet (on an M-LAG pair the platform picks ::1, ::2 and ::3 as for IPv4).
  const [address6, setAddress6] = useState("");
  const [hop6, setHop6] = useState("");
  const [edit6, setEdit6] = useState<{ name: string; address: string; next_hop: string } | null>(null);
  // A service on an M-LAG trunk: a subnet (the platform picks .1/.2/.3) or each address typed.
  const [typed, setTyped] = useState(false);
  const [own, setOwn] = useState("");
  const [peerAddr, setPeerAddr] = useState("");
  const targets = useFetch(() => fetchRoutingTargets(site), [site]);
  const peer = (targets.data ?? [])
    .find((t) => t.devices.length === 2 && t.devices.some((d) => d.name === form.device))
    ?.devices.find((d) => d.name !== form.device)?.name;
  // Only an M-LAG trunk makes the service the pair's; a single trunk is like a physical port.
  const onTrunk = !!ports.find((p) => p.name === form.port)?.mlag;

  useEffect(() => {
    setPorts([]);
    if (!form.device) return;
    fetchPorts(form.device, true)
      // A service port carries no segments (a physical port, a single trunk, or an M-LAG trunk: the
      // pair's service); it may carry other services, each in its own VLAN.
      .then((all) => setPorts(all.filter((p) => p.segments.length === 0)))
      .catch((e) => setError(`Could not list ${form.device}'s ports: ${message(e)}`));
  }, [form.device]);

  const firstFirewall = services.find((s) => s.kind === "firewall")?.name;
  // An existing VPN instance to join: another service's (made on this switch too when it is not
  // there yet: a second ISP link on the other border spine), or a routed segment's.
  const instanceOf = (s: Service) => s.vrf ?? s.name;
  const here = new Set(services.filter((s) => (s.devices ?? [s.device]).includes(form.device)).map(instanceOf));
  const sharedHere = [...new Set(services.map(instanceOf))].sort()
    .filter((v) => !segments.some((g) => `${g.name}-${g.bd_id}` === v));
  const segmentInstances = segments.filter((g) => g.routing).map((g) => ({ vrf: `${g.name}-${g.bd_id}`, name: g.name }));
  const set = (field: keyof Service) => (e: { target: { value: string } }) =>
    setForm({ ...form, [field]: e.target.value, ...(field === "device" ? { port: "", vrf: null } : {}) });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const parsed = parseAsn(asn);
    if (!parsed.ok) return setError(parsed.error);
    const addresses = onTrunk && typed && peer ? { [form.device]: own.trim(), [peer]: peerAddr.trim() } : null;
    const ipv6 = address6.trim() ? { address: address6.trim(), next_hop: hop6.trim() } : null;
    try {
      await createService(site, { ...form, vlan: Number(vlan), asn: form.kind === "isp" ? null : parsed.asn, addresses, ipv6 });
      onDone(`${form.kind === "isp" ? "ISP" : "Firewall"} service ${form.name} created.`);
      setForm(EMPTY);
      setVlan("");
      setAsn("");
      setOwn("");
      setPeerAddr("");
      setAddress6("");
      setHop6("");
    } catch (e) {
      setError(message(e));
    }
  };

  const saveIpv6 = async (name: string, ipv6: { address: string; next_hop: string } | null) => {
    setError(null);
    try {
      await setServiceIpv6(site, name, ipv6);
      setEdit6(null);
      onDone(ipv6 ? `IPv6 of ${name} saved.` : `${name} is IPv4 only again.`);
    } catch (e) {
      setError(message(e));
    }
  };

  const changeAsn = async (service: Service) => {
    const answer = window.prompt(`Firewall ASN for ${service.name} (empty = no BGP session)`, service.asn?.toString() ?? "");
    if (answer === null) return;
    const parsed = parseAsn(answer);
    if (!parsed.ok) return setError(parsed.error);
    setError(null);
    try {
      await setServiceAsn(site, service.name, parsed.asn);
      onDone(parsed.asn === null ? `BGP with ${service.name} removed.` : `${service.name} peers with AS ${parsed.asn}.`);
    } catch (e) {
      setError(message(e));
    }
  };

  const remove = async (name: string) => {
    if (!window.confirm(`Delete firewall service ${name}?`)) return;
    setError(null);
    try {
      await deleteService(site, name);
      onDone(`Firewall service ${name} deleted.`);
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <div className="diff-card">
      <h2>Services</h2>
      <p className="muted">
        A firewall or an ISP router cabled to a leaf port, reached over a plain VLAN interface in its own VPN instance, or one it shares with another service or a routed segment
        (no VXLAN). Routed segments send their default route to a firewall; the first firewall is the default for new
        routed segments. With an ASN, the leaf also runs eBGP (with BFD) with the firewall. An ISP service is the
        container only: its BGP session, with import and export policies, is set in the routing intent.
      </p>
      {services.length > 0 && (
        <table className="devices">
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
              <th>Leaf</th>
              <th>Port</th>
              <th>VLAN</th>
              <th>VPN instance</th>
              <th>Address</th>
              <th>Next hop</th>
              <th>IPv6</th>
              <th>BGP</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {services.map((s) => (
              <tr key={s.name}>
                <td>
                  {s.name}
                  {s.kind === "firewall" && s.name === firstFirewall && <span className="pill">default</span>}
                </td>
                <td>{s.kind === "isp" ? "ISP" : "firewall"}</td>
                <td>{(s.devices ?? [s.device]).join(" + ")}</td>
                <td className="mono">{s.port}</td>
                <td>{s.vlan}</td>
                <td className="mono">
                  {instanceOf(s)}
                  {instanceOf(s) !== s.name && <span className="muted small"> (shared)</span>}
                </td>
                <td className="mono">{s.address}</td>
                <td className="mono">{s.next_hop}</td>
                <td className="mono">
                  {edit6?.name === s.name ? (
                    <form className="form-row" onSubmit={(e) => { e.preventDefault(); void saveIpv6(s.name, { address: edit6.address.trim(), next_hop: edit6.next_hop.trim() }); }}>
                      <input aria-label="IPv6 subnet" value={edit6.address} onChange={(e) => setEdit6({ ...edit6, address: e.target.value })} placeholder="fd00:255::/64" />
                      <input aria-label="IPv6 next hop" value={edit6.next_hop} onChange={(e) => setEdit6({ ...edit6, next_hop: e.target.value })} placeholder="fd00:255::2" />
                      <button type="submit" className="primary" disabled={!edit6.address.trim() || !edit6.next_hop.trim()}>Save</button>
                      <button type="button" onClick={() => setEdit6(null)}>Cancel</button>
                    </form>
                  ) : s.ipv6 ? (
                    <span title={s.ipv6.addresses ? Object.entries(s.ipv6.addresses).map(([l, a]) => `${l} ${a}`).join(", ") : undefined}>
                      {s.ipv6.address}{s.ipv6.addresses ? " (M-LAG)" : ""} → {s.ipv6.next_hop}
                    </span>
                  ) : (
                    <span className="muted">none</span>
                  )}
                </td>
                <td>
                  {s.kind === "isp" ? (
                    <span className="muted">routing intent</span>
                  ) : s.asn === null ? (
                    <span className="muted">none</span>
                  ) : (
                    `AS ${s.asn}`
                  )}
                </td>
                <td>
                  {canOperate && (
                  <div className="actions">
                    {s.kind === "firewall" && (
                      <button onClick={() => changeAsn(s)}>{s.asn === null ? "Set ASN" : "Change ASN"}</button>
                    )}
                    {edit6?.name !== s.name && (
                      <button onClick={() => setEdit6({ name: s.name, address: s.ipv6?.address ?? "", next_hop: s.ipv6?.next_hop ?? "" })}>
                        {s.ipv6 ? "Change IPv6" : "Set IPv6"}
                      </button>
                    )}
                    {s.ipv6 && <button onClick={() => saveIpv6(s.name, null)}>Remove IPv6</button>}
                    <button onClick={() => remove(s.name)}>Delete</button>
                  </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canOperate && (
      <form className="form-row" onSubmit={submit}>
        <label>
          Kind
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Service["kind"] })}>
            <option value="firewall">firewall</option>
            <option value="isp">ISP</option>
          </select>
        </label>
        <label>
          Name <input value={form.name} onChange={set("name")} placeholder={form.kind === "isp" ? "ISP-A" : "FW"} />
        </label>
        <label>
          Switch
          <select value={form.device} onChange={set("device")}>
            <option value="">choose…</option>
            {leaves.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        {form.device && (
          <div className="attach-plate">
            <span className="muted small">Click the service's port on {form.device} (a trunk member picks its trunk):</span>
            <Faceplate
              device={form.device}
              refresh={ports.length}
              pick={{
                selected: form.port ? [form.port] : [],
                refuse: (p) => refusal(p, new Set(ports.map((x) => x.name)), trunkOrPort, "carries segments or is not usable for a service"),
                target: trunkOrPort,
                onToggle: (t) => setForm({ ...form, port: form.port === t ? "" : t }),
              }}
            />
            {form.port && <div className="picked-chips"><span key={form.port}>{form.port}</span></div>}
          </div>
        )}
        <label>
          VLAN <input value={vlan} onChange={(e) => setVlan(e.target.value)} inputMode="numeric" placeholder="3000" />
        </label>
        <label title="Its own new VPN instance named after it, or an existing one: another service's (created on this switch too if it is not here yet, with the same route distinguisher VLAN), or a routed segment's (a firewall there can only be that segment's exit)">
          VPN instance
          <select value={form.vrf ?? ""} onChange={(e) => setForm({ ...form, vrf: e.target.value || null })} disabled={!form.device}>
            <option value="">its own (new)</option>
            {sharedHere.length > 0 && (
              <optgroup label="Another service's">
                {sharedHere.map((v) => <option key={v} value={v}>{v}{here.has(v) ? "" : " (new on this switch)"}</option>)}
              </optgroup>
            )}
            {segmentInstances.length > 0 && (
              <optgroup label="A segment's">
                {segmentInstances.map((g) => <option key={g.vrf} value={g.vrf}>{g.vrf} (segment {g.name})</option>)}
              </optgroup>
            )}
          </select>
        </label>
        {onTrunk ? (
          <>
            <fieldset>
              <legend>M-LAG pair addressing</legend>
              <label className="check">
                <input type="radio" name="pair-addressing" checked={!typed} onChange={() => setTyped(false)} />
                subnet: the platform picks .1, .2 and .3
              </label>
              <label className="check">
                <input type="radio" name="pair-addressing" checked={typed} onChange={() => setTyped(true)} disabled={!peer} />
                type each address
              </label>
            </fieldset>
            {!typed ? (
              <label title="M-LAG pair: .1 and .2 for the leaves, .3 shared M-LAG address">
                Subnet <input id="svc-subnet" value={form.address} onChange={set("address")} placeholder="10.255.0.0/29" />
              </label>
            ) : (
              <>
                <label>
                  {form.device} address <input id="svc-own" value={own} onChange={(e) => setOwn(e.target.value)} placeholder="10.255.0.1/29" />
                </label>
                <label>
                  {peer} address <input id="svc-peer" value={peerAddr} onChange={(e) => setPeerAddr(e.target.value)} placeholder="10.255.0.2/29" />
                </label>
                <label>
                  Shared M-LAG address <input id="svc-mlag" value={form.address} onChange={set("address")} placeholder="10.255.0.3/29" />
                </label>
              </>
            )}
          </>
        ) : (
          <label>
            Address <input value={form.address} onChange={set("address")} placeholder="10.255.0.1/30" />
          </label>
        )}
        <label>
          {form.kind === "isp" ? "ISP router IP" : "Firewall IP"}{" "}
          <input value={form.next_hop} onChange={set("next_hop")} placeholder="10.255.0.2" />
        </label>
        {(
          <>
            <label title={onTrunk ? "Optional: ::1 and ::2 for the leaves, ::3 shared M-LAG address" : "Optional: the service's IPv6 subnet (the platform picks ::1)"}>
              IPv6 subnet <input value={address6} onChange={(e) => setAddress6(e.target.value)} placeholder="optional, fd00:255::/64" />
            </label>
            <label>
              IPv6 {form.kind === "isp" ? "router" : "firewall"} IP{" "}
              <input value={hop6} onChange={(e) => setHop6(e.target.value)} placeholder="fd00:255::2" disabled={!address6.trim()} />
            </label>
          </>
        )}
        {form.kind === "firewall" && (
          <label>
            Firewall ASN
            <input value={asn} onChange={(e) => setAsn(e.target.value)} inputMode="numeric" placeholder="empty = no BGP" />
          </label>
        )}
        <button
          type="submit"
          className="primary"
          disabled={!form.name || !form.device || !form.port || !vlan || !form.address || !form.next_hop || (onTrunk && typed && (!own || !peerAddr)) || (!!address6.trim() && !hop6.trim())}
        >
          Create
        </button>
      </form>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export function SegmentRouting({
  segment,
  segments,
  services,
  onDone,
  centralized = false,
}: {
  segment: Segment;
  segments: Segment[];
  services: Service[];
  onDone: (t: string) => void;
  centralized?: boolean; // no L3 VNI, gateways on the gateway pair
}) {
  const routing = segment.routing;
  const canOperate = useCan("operator");
  const [editing, setEditing] = useState(false);
  const [prefixLength, setPrefixLength] = useState("24");
  const [firewall, setFirewall] = useState("");
  const [peers, setPeers] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const others = segments.filter((s) => s.fabric && s.vni !== segment.vni && s.routing);
  const firewalls = services.filter((s) => s.kind === "firewall"); // ISP services carry no default route
  const fabric = segment.fabric ?? "";

  // Start from the current intent; a new routed segment uses the fabric's first service.
  const edit = () => {
    setFirewall(routing ? routing.firewall ?? "" : firewalls[0]?.name ?? "");
    setPeers(routing?.peers ?? []);
    setError(null);
    setEditing(true);
  };

  const name = (vni: number) => segments.find((s) => s.vni === vni)?.name ?? `VNI ${vni}`;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await setRouting(fabric, segment.vni, {
        routed: true,
        prefix_length: Number(prefixLength),
        firewall: firewall || null,
        peers,
      });
      setEditing(false);
      onDone(`Routing of ${segment.name} saved.`);
    } catch (e) {
      setError(message(e));
    }
  };

  const unroute = async () => {
    if (!window.confirm(`Stop routing ${segment.name}? Its gateway subnet is released.`)) return;
    setError(null);
    try {
      await setRouting(fabric, segment.vni, { routed: false });
      onDone(`${segment.name} is no longer routed.`);
    } catch (e) {
      setError(message(e));
    }
  };

  if (!segment.fabric) return null;

  return (
    <div className="routing">
      {routing && !editing && (
        <p>
          <strong>Routed</strong> · gateway <span className="mono">{routing.gateway}</span> · VPN instance{" "}
          <span className="mono">{segment.name}-{segment.bd_id}</span>
          {centralized ? " · on the gateway pair" : ` · L3 VNI ${segment.vni + L3_VNI_OFFSET}`} · default route{" "}
          {routing.firewall ? `via firewall ${routing.firewall}` : "none"}
          {routing.peers.length > 0 && <> · peered with {routing.peers.map(name).join(", ")}</>}
        </p>
      )}
      {!routing && !editing && <p className="muted">Not routed (layer 2 only).</p>}
      {!editing && canOperate && (
        <div className="actions">
          <button onClick={edit}>{routing ? "Change routing" : "Route"}</button>
          {routing && <button onClick={unroute}>Stop routing</button>}
        </div>
      )}
      {editing && (
        <form className="form-row" onSubmit={save}>
          {!routing && (
            <label>
              Gateway subnet /
              <input value={prefixLength} onChange={(e) => setPrefixLength(e.target.value)} inputMode="numeric" />
            </label>
          )}
          <label>
            Default route
            <select value={firewall} onChange={(e) => setFirewall(e.target.value)}>
              <option value="">none</option>
              {firewalls.map((s) => (
                <option key={s.name} value={s.name}>
                  via firewall {s.name}
                </option>
              ))}
            </select>
          </label>
          <fieldset>
            <legend>Peer directly with</legend>
            {others.length === 0 && <span className="muted">no other routed segment</span>}
            {others.map((s) => (
              <label key={s.vni} className="check">
                <input
                  type="checkbox"
                  checked={peers.includes(s.vni)}
                  onChange={(e) =>
                    setPeers(e.target.checked ? [...peers, s.vni] : peers.filter((p) => p !== s.vni))
                  }
                />
                {s.name}
              </label>
            ))}
          </fieldset>
          <button type="submit" className="primary">
            Save
          </button>
          <button type="button" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
