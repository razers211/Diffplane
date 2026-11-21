import { useState } from "react";
import {
  fetchBgp, fetchBgpSimulation, fetchRoutes, type BgpRoute, type BgpSimulation, type Simulated,
} from "./api";
import { bgpInstances, choices, filterRoutes, page, pageCount, type RouteFilter } from "./routeView";
import { useFetch } from "./useFetch";

// Route view: a switch's routing tables and BGP routes, read from the
// switch when asked (nothing stored, nothing configured). A peer's routes are shown with and
// without its policies: the platform evaluates its own routing intent on them (simulated).

const clean = (e: string | null) => e?.replace(/^\d+: /, "") ?? null;

/** A switch's Routing table tab: its routing tables and BGP, read from it when shown. */
export function Routes({ device, readable }: { device: string; readable: boolean }) {
  const [tab, setTab] = useState<"rib" | "bgp">("rib");

  return (
    <section className="routes">
      <div className="toolbar">
        <span className="toggle" role="group" aria-label="Table">
          <button aria-pressed={tab === "rib"} onClick={() => setTab("rib")}>Routing table</button>
          <button aria-pressed={tab === "bgp"} onClick={() => setTab("bgp")}>BGP</button>
        </span>
      </div>
      <p className="muted">
        Read from the switch now, over its NETCONF session; nothing is stored or changed. Only active switches with pinned
        host keys can be read.
      </p>
      {!readable && <p className="empty">{device} is not active with pinned host keys: nothing can be read from it.</p>}
      {readable && tab === "rib" && <RoutingTable key={device} device={device} />}
      {readable && tab === "bgp" && <Bgp key={device} device={device} />}
    </section>
  );
}

function RoutingTable({ device }: { device: string }) {
  const rib = useFetch(() => fetchRoutes(device), [device]);
  const [f, setF] = useState<RouteFilter>({ vpn: "", protocol: "", text: "", inactive: true });
  const [n, setN] = useState(0);
  const routes = rib.data?.routes ?? [];
  const shown = filterRoutes(routes, f);
  const set = (d: Partial<RouteFilter>) => { setF({ ...f, ...d }); setN(0); };

  return (
    <div className="panel">
      <header>
        <h2>Routing table</h2>
        <span className="hint">{rib.loading ? "reading…" : `${routes.length} routes`}</span>
        <span className="inline actions-right"><button className="small" onClick={rib.reload}>Read again</button></span>
      </header>
      {rib.error && <p className="error">{clean(rib.error)}</p>}
      <div className="instance-chips" role="group" aria-label="Instance">
        {["", ...choices(routes, "vpn")].map((v) => (
          <button key={v || "all"} aria-pressed={f.vpn === v} onClick={() => set({ vpn: v })}>
            <span className={v ? "mono" : ""}>{v || "All instances"}</span>
            <span className="n">{v ? routes.filter((r) => r.vpn === v).length : routes.length}</span>
          </button>
        ))}
      </div>
      <div className="filters">
        <select value={f.protocol} onChange={(e) => set({ protocol: e.target.value })} aria-label="Protocol">
          <option value="">every protocol</option>
          {choices(routes, "protocol").map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <input value={f.text} onChange={(e) => set({ text: e.target.value })} placeholder="prefix, next hop or interface" />
        <label className="check"><input type="checkbox" checked={f.inactive} onChange={(e) => set({ inactive: e.target.checked })} /> inactive routes</label>
        <span className="count">{shown.length} shown</span>
      </div>
      <div className="tablewrap">
        <table className="devices rib">
          <thead>
            <tr>
              <th>Prefix</th>
              {!f.vpn && <th>Instance</th>}
              <th>Protocol</th>
              <th>Next hop → interface</th>
              <th className="num">Preference</th>
              <th className="num">Cost</th>
              <th>State</th>
              <th className="num">Age</th>
            </tr>
          </thead>
          <tbody>
            {page(shown, n).map((r, i) => (
              <tr key={`${r.vpn} ${r.prefix} ${r.protocol} ${r.next_hop} ${i}`} className={r.active ? "" : "inactive"}>
                <td className="mono prefix">{r.prefix}</td>
                {!f.vpn && <td className="mono small">{r.vpn}</td>}
                <td><span className={`proto proto-${r.protocol.toLowerCase().replace(/[^a-z]/g, "")}`}>{r.protocol}{r.process ? ` ${r.process}` : ""}</span></td>
                <td className="mono">
                  {r.next_hop}
                  {r.interface && <span className="faint"> → </span>}
                  {r.interface}
                </td>
                <td className="num">{r.preference}</td>
                <td className="num">{r.cost}</td>
                <td><span className={`state ${r.active ? "on" : "off"}`}>{r.active ? "active" : "inactive"}</span></td>
                <td className="num mono small">{r.age}</td>
              </tr>
            ))}
            {!rib.loading && shown.length === 0 && <tr><td colSpan={8} className="empty">No route matches.</td></tr>}
          </tbody>
        </table>
      </div>
      <Pager n={n} count={pageCount(shown.length)} onChange={setN} />
    </div>
  );
}

function Pager({ n, count, onChange }: { n: number; count: number; onChange: (n: number) => void }) {
  if (count <= 1) return null;
  return (
    <div className="inline pager">
      <button className="small" disabled={n === 0} onClick={() => onChange(n - 1)}>← Previous</button>
      <span className="muted small">page {n + 1} of {count}</span>
      <button className="small" disabled={n + 1 >= count} onClick={() => onChange(n + 1)}>Next →</button>
    </div>
  );
}

function Bgp({ device }: { device: string }) {
  const rib = useFetch(() => fetchRoutes(device), [device]);
  const [vpn, setVpn] = useState<string | null>(null);
  const bgp = useFetch(() => fetchBgp(device, vpn), [device, vpn]);
  const [peer, setPeer] = useState<string | null>(null);
  const [n, setN] = useState(0);
  const routes = bgp.data?.routes ?? [];
  const instances = bgpInstances(rib.data?.routes ?? []);

  return (
    <>
      <div className="panel">
        <header>
          <h2>BGP peers</h2>
          <span className="hint">{bgp.loading ? "reading…" : `local AS ${bgp.data?.local_asn ?? "—"}`}</span>
          <span className="inline actions-right"><button className="small" onClick={bgp.reload}>Read again</button></span>
        </header>
        {bgp.error && <p className="error">{clean(bgp.error)}</p>}
        <div className="instance-chips" role="group" aria-label="Instance">
          {instances.map((v) => (
            <button key={v ?? ""} aria-pressed={vpn === v} onClick={() => { setVpn(v); setPeer(null); setN(0); }}>
              <span className={v ? "mono" : ""}>{v ?? "Global table"}</span>
            </button>
          ))}
        </div>
        <div className="tablewrap">
          <table className="devices rib">
            <thead>
              <tr><th>Peer</th><th className="num">AS</th><th>State</th><th className="num">Up/down</th><th className="num">Prefixes</th><th>Policies (routing intent)</th><th /></tr>
            </thead>
            <tbody>
              {(bgp.data?.peers ?? []).map((p) => (
                <tr key={p.address} className={peer === p.address ? "open" : ""}>
                  <td className="mono prefix">{p.address}</td>
                  <td className="num">{p.asn}</td>
                  <td><span className={`state ${p.state === "Established" ? "on" : "warn"}`}>{p.state}</span></td>
                  <td className="num mono small">{p.up_down}</td>
                  <td className="num">{p.prefixes}</td>
                  <td className="small">
                    {p.intent ? policyText(p.intent) : <span className="faint">not in routing intent (fabric or service peer)</span>}
                  </td>
                  <td><button className="linklike small" onClick={() => setPeer(peer === p.address ? null : p.address)}>
                    {peer === p.address ? "Close" : "Routes with/without policies"}
                  </button></td>
                </tr>
              ))}
              {!bgp.loading && (bgp.data?.peers ?? []).length === 0 && <tr><td colSpan={7} className="empty">No BGP peer here.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {peer && <PeerSimulation key={`${vpn} ${peer}`} device={device} vpn={vpn} peer={peer} />}
      <div className="panel">
        <header><h2>BGP routing table</h2><span className="hint">{routes.length} paths</span></header>
        <BgpTable rows={page(routes, n)} />
        <Pager n={n} count={pageCount(routes.length)} onChange={setN} />
      </div>
    </>
  );
}

const policyText = (i: { import_policy: string | null; export_policy: string | null; import_prefix_list: string | null; export_prefix_list: string | null }) => {
  const parts = [
    i.import_prefix_list && `import ip-prefix ${i.import_prefix_list}`, i.import_policy && `import ${i.import_policy}`,
    i.export_prefix_list && `export ip-prefix ${i.export_prefix_list}`, i.export_policy && `export ${i.export_policy}`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "none";
};

function BgpTable({ rows, extra }: { rows: (BgpRoute & { simulated?: Simulated; advertised?: boolean; ibgp_rule?: boolean })[]; extra?: "export" | "import" }) {
  return (
    <div className="tablewrap">
      <table className="devices rib">
        <thead>
          <tr>
            <th>Status</th><th>Network</th><th>Next hop</th><th className="num">MED</th><th className="num">LocPrf</th><th>AS path</th><th>Origin</th>
            {extra && <th>With policies (simulated)</th>}
            {extra === "export" && <th>Advertised now</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.prefix} ${r.next_hop} ${i}`} className={r.best ? "" : "inactive"}>
              <td><span className={`proto ${r.best ? "proto-bgp" : ""}`} title={r.best ? "best path" : "not the best path"}>{r.status}</span></td>
              <td className="mono prefix">{r.prefix}</td>
              <td className="mono">{r.next_hop}</td>
              <td className="num">{r.med ?? ""}</td>
              <td className="num">{r.local_preference ?? ""}</td>
              <td className="mono small">{r.as_path}</td>
              <td className="mono">{r.origin ?? ""}</td>
              {extra && r.simulated && (
                <td>
                  <span className={`pill ${r.simulated.accepted ? "good" : "bad"}`}>{r.simulated.accepted ? "accepted" : "rejected"}</span>
                  {r.simulated.changes.local_preference !== undefined && <span className="pill info">local-preference {r.simulated.changes.local_preference}</span>}
                  {r.ibgp_rule && <span className="pill warnpill">iBGP to iBGP: not sent unless reflected</span>}
                  <div className="small faint">{r.simulated.by.join(" → ") || "no policy"}</div>
                </td>
              )}
              {extra === "export" && <td>{r.advertised ? <span className="pill good">yes</span> : <span className="pill">no</span>}</td>}
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={extra === "export" ? 9 : extra ? 8 : 7} className="empty">No route.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function PeerSimulation({ device, vpn, peer }: { device: string; vpn: string | null; peer: string }) {
  const sim = useFetch(() => fetchBgpSimulation(device, vpn, peer), [device, vpn, peer]);
  const [withPolicies, setWithPolicies] = useState(true);
  const data: BgpSimulation | null = sim.data;
  const exported = data ? (withPolicies ? data.export.filter((r) => r.simulated.accepted) : data.export) : [];
  const mismatches = data ? data.export.filter((r) => r.simulated.accepted && !r.ibgp_rule && r.advertised !== r.simulated.accepted) : [];

  return (
    <div className="panel">
      <header>
        <h2>Peer {peer}{vpn ? ` in ${vpn}` : ""}</h2>
        <span className="toggle" role="group" aria-label="Policies">
          <button aria-pressed={withPolicies} onClick={() => setWithPolicies(true)}>With policies</button>
          <button aria-pressed={!withPolicies} onClick={() => setWithPolicies(false)}>Without policies</button>
        </span>
        <span className="inline actions-right"><button className="small" onClick={sim.reload}>Read again</button></span>
      </header>
      {sim.error && <p className="error">{clean(sim.error)}</p>}
      {sim.loading && <p className="muted">Reading the switch…</p>}
      {data && (
        <>
          <p className="muted">
            {data.intent ? <>Routing intent of <b>{data.target}</b>: {policyText(data.intent)}.</> : "This peer has no policies in routing intent: with and without policies are the same."}
            {" "}Simulated by the platform from its own routing intent; nothing is changed on the switch.
          </p>
          {Object.entries(data.policies).length > 0 && (
            <details>
              <summary className="small">Policies used</summary>
              <pre className="config">{Object.entries(data.policies).map(([name, rules]) => `${name}\n${rules.map((r) => `  ${JSON.stringify(r)}`).join("\n")}`).join("\n")}</pre>
            </details>
          )}
          <h3>Export: {withPolicies ? "what the policies let through" : "every best route, before the export policy"} ({exported.length})</h3>
          {data.peer.state !== "Established" && <p className="muted small">The session is {data.peer.state}: the switch advertises nothing now.</p>}
          {data.peer.state === "Established" && mismatches.length > 0 && (
            <p className="warning small">{mismatches.length} route(s) the simulation accepts are not advertised now (the switch may filter for other reasons: next hop, loop prevention).</p>
          )}
          <BgpTable rows={exported} extra="export" />
          <h3>Import: routes accepted from this peer ({data.import.length})</h3>
          <p className="muted small">
            Without keep-all-routes the switch keeps only what its import policy accepted: routes the policy rejected are not visible.
            Each accepted route shows the node that accepted it and what it set.
          </p>
          <BgpTable rows={data.import} extra="import" />
        </>
      )}
    </div>
  );
}
