import { useEffect } from "react";
import { fetchControlPlane, type ControlPlane as Plane } from "./api";
import { useFetch } from "./useFetch";

// A switch's control plane: BGP and EVPN peers, OSPF neighbours, M-LAG,
// VXLAN tunnels, route and MAC counts, as the collector read them (every 30 s). A partner that is
// planned is expected to be down: shown grey, not as a problem.

const REFRESH_MS = 30_000;

function since(seconds: number | null): string {
  if (seconds === null) return "—";
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function State({ up, expected, text }: { up: boolean | null; expected?: boolean; text: string }) {
  const cls = up ? "pill good" : expected ? "pill" : "pill bad";
  return <span className={cls} title={expected ? "its partner is planned: down is expected" : undefined}>{text}</span>;
}

export function ControlPlane({ name }: { name: string }) {
  const data = useFetch(() => fetchControlPlane(name), [name]);
  useEffect(() => {
    const id = window.setInterval(data.reload, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [data.reload]);
  if (data.error) return <p className="error">{data.error}</p>;
  if (!data.data) return <p className="muted">Loading…</p>;
  const p: Plane = data.data;
  if (!p.collected) {
    return <p className="empty muted">Nothing collected yet: the collector reads the control plane of active, pinned switches every 30 s.</p>;
  }
  const routeTotal = (vpn: string) => Object.values(p.routes[vpn] ?? {}).reduce((t, c) => t + (c.total ?? 0), 0);

  return (
    <div className="control-plane" data-tour="control-plane">
      {p.problems.length > 0 && (
        <div className="notice error-box">
          <b>Needs a look:</b>
          <ul>{p.problems.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      <section className="panel">
        <header><h2>BGP peers</h2><span className="hint">per address family · read every 30 s</span></header>
        {p.bgp.length === 0 ? <p className="empty muted">No BGP peer.</p> : (
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th>Peer</th><th>Switch</th><th>VPN instance</th><th>Family</th><th>State</th><th>Since</th>
                <th className="num">Received</th><th className="num">Active</th><th className="num">Advertised</th></tr></thead>
              <tbody>
                {p.bgp.map((b) => (
                  <tr key={`${b.vpn}-${b.peer}-${b.family}`}>
                    <td className="mono">{b.peer}</td>
                    <td>{b.switch ?? <span className="faint">outside the fabric</span>}</td>
                    <td className="mono small">{b.vpn}</td>
                    <td>{b.family === "ipv4uni" ? "IPv4" : b.family === "evpn" ? "EVPN" : b.family}</td>
                    <td><State up={b.up} expected={b.expected_down} text={b.state} />{b.expected_down && <span className="faint small"> planned</span>}</td>
                    <td>{since(b.seconds)}</td>
                    <td className="num">{b.prefixes.received ?? "—"}</td>
                    <td className="num">{b.prefixes.active ?? "—"}</td>
                    <td className="num">{b.prefixes.advertised ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="cols">
        {p.mlag && (
          <section className="panel">
            <header><h2>M-LAG</h2><span className="hint">with {p.mlag.partner ?? "its peer"}{p.mlag.expected_down ? " (planned)" : ""}</span></header>
            <dl className="settings empty">
              <div><dt>DFS</dt><dd>{p.mlag.role}, {p.mlag.state}{p.mlag.causation ? <span className="muted"> ({p.mlag.causation})</span> : null}</dd></div>
              <div><dt>Heartbeat</dt><dd><State up={p.mlag.heartbeat} expected={p.mlag.expected_down} text={p.mlag.heartbeat ? "ok" : "lost"} /></dd></div>
              {Object.entries(p.mlag.peer_links).map(([i, up]) => (
                <div key={i}><dt>Peer-link {i}</dt><dd><State up={up} expected={p.mlag!.expected_down} text={up ? "up" : "down"} /></dd></div>
              ))}
              {Object.entries(p.mlag.ports).map(([i, up]) => (
                <div key={i}><dt>{i}</dt><dd><State up={up} expected text={up ? "up" : "down"} /></dd></div>
              ))}
            </dl>
          </section>
        )}
        <section className="panel">
          <header><h2>Tables</h2><span className="hint">route and MAC counts</span></header>
          <dl className="settings empty">
            {Object.keys(p.routes).map((vpn) => (
              <div key={vpn}>
                <dt className="mono small">{vpn}</dt>
                <dd>{routeTotal(vpn)} routes <span className="muted small">({Object.entries(p.routes[vpn]!).map(([proto, c]) => `${proto} ${c.total}`).join(", ")})</span></dd>
              </div>
            ))}
            {"total" in p.macs && (
              <div><dt>MAC addresses</dt><dd>{p.macs.total} of {p.macs.capacity ?? "?"} <span className="muted small">
                ({p.macs["dynamic-local"] ?? 0} local, {p.macs["dynamic-remote"] ?? 0} remote)</span></dd></div>
            )}
          </dl>
        </section>
      </div>
      <div className="cols">
        <section className="panel">
          <header><h2>OSPF neighbours</h2></header>
          {p.ospf.length === 0 ? <p className="empty muted">No OSPF neighbour.</p> : (
            <ul className="changelist">
              {p.ospf.map((o) => (
                <li key={`${o.vpn}-${o.interface}-${o.neighbor}`}>
                  <span><span className="mono">{o.neighbor}</span> {o.switch && <span className="muted">({o.switch})</span>} on <span className="mono">{o.interface}</span></span>
                  <State up={o.up} text={o.state} />
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="panel">
          <header><h2>VXLAN tunnels</h2><span className="hint">to other VTEPs</span></header>
          {p.tunnels.length === 0 ? <p className="empty muted">No tunnel: no other VTEP is reachable yet.</p> : (
            <ul className="changelist">
              {p.tunnels.map((t) => (
                <li key={t.destination}>
                  <span><span className="mono">{t.destination}</span> {t.switch && <span className="muted">({t.switch})</span>}</span>
                  <State up={t.up} text={t.up ? "up" : "down"} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {p.refused.length > 0 && <p className="muted small">This switch does not answer: {p.refused.join(", ")} (left out).</p>}
    </div>
  );
}
