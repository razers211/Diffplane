import { useState } from "react";
import { fetchFlows, type FlowKind } from "./api";
import { href } from "./nav";
import { formatBps } from "./units";
import { useFetch } from "./useFetch";

// Flows: sampled NetStream (1 packet in 1024), scaled back up. Edge ports
// (servers, services) are the totals; uplinks (VXLAN inner) are a second view, because a flow between
// two leaves is sampled at both. Overview › Flows for the fabric, a Flows tab per switch.

const WINDOWS: [number, string][] = [[15, "15 min"], [60, "1 h"], [360, "6 h"], [1440, "24 h"]];
const PROTOCOLS: Record<number, string> = { 1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP", 58: "ICMPv6", 132: "SCTP" };

/** "TCP 443", "ICMP", "proto 89". */
export function protocolName(proto: number, port: number): string {
  const name = PROTOCOLS[proto] ?? `proto ${proto}`;
  return port && (proto === 6 || proto === 17 || proto === 132) ? `${name} ${port}` : name;
}

const segment = (name: string) => name || <span className="faint">outside segments</span>;

export function FlowsView({ site, device }: { site: string; device?: string }) {
  const [minutes, setMinutes] = useState(60);
  const [kind, setKind] = useState<FlowKind>("edge");
  const data = useFetch(() => (site ? fetchFlows(site, minutes, kind, device) : Promise.resolve(null)), [site, minutes, kind, device]);
  if (!site) return <p className="empty muted">Pick a fabric (top right): flows are shown per fabric.</p>;
  const f = data.data;

  return (
    <div className="flows-page">
      <section className="panel" data-tour="flows-filters">
        <header>
          <h2>{device ? `Flows through ${device}` : "Flows"}</h2>
          <span className="toggle" role="group" aria-label="Where sampled">
            <button aria-pressed={kind === "edge"} onClick={() => setKind("edge")}>Edge ports</button>
            <button aria-pressed={kind === "vxlan"} onClick={() => setKind("vxlan")}>Uplinks (VXLAN)</button>
          </span>
          <span className="toggle" role="group" aria-label="Window">
            {WINDOWS.map(([m, label]) => <button key={m} aria-pressed={minutes === m} onClick={() => setMinutes(m)}>{label}</button>)}
          </span>
        </header>
        <p className="pad muted small">
          {kind === "edge" ? "Traffic as it enters the fabric from servers and services: the totals." :
            "Overlay traffic as it arrives at a leaf from another one: the same flows again where they cross leaves."}{" "}
          Sampled 1 packet in 1024, scaled back up: small or short flows may not show.
        </p>
        {data.error && <p className="error">{data.error}</p>}
        {f && f.total.records === 0 && (
          <p className="empty muted">No flows in this window. NetStream may be off for this fabric (Fabric page), or the ports carry no traffic.</p>
        )}
        {f && f.total.records > 0 && (
          <p className="pad">
            <b className="num">{formatBps(f.total.bps)}</b> on average over the window, from {f.total.records} sampled flow records.
          </p>
        )}
      </section>
      {f && f.total.records > 0 && (
        <div className="cols">
          <section className="panel" data-tour="flows-talkers">
            <header><h2>Top talkers</h2><span className="hint">by source address</span></header>
            <div className="tablewrap">
              <table className="devices">
                <thead><tr><th>Address</th><th>Segment</th><th>Average</th></tr></thead>
                <tbody>
                  {f.talkers.map((t) => (
                    <tr key={t.address}><td className="mono">{t.address}</td><td>{segment(t.segment)}</td><td className="num">{formatBps(t.bps)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel" data-tour="flows-segments">
            <header><h2>Between segments</h2></header>
            <div className="tablewrap">
              <table className="devices">
                <thead><tr><th>From</th><th>To</th><th>Average</th></tr></thead>
                <tbody>
                  {f.segments.map((s) => (
                    <tr key={`${s.src_segment}>${s.dst_segment}`}><td>{segment(s.src_segment)}</td><td>{segment(s.dst_segment)}</td>
                      <td className="num">{formatBps(s.bps)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
      {f && f.total.records > 0 && (
        <section className="panel" data-tour="flows-conversations">
          <header><h2>Conversations</h2><span className="hint">source, destination and service</span></header>
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th>From</th><th>To</th><th>Service</th><th>Average</th><th>Packets</th></tr></thead>
              <tbody>
                {f.conversations.map((c) => (
                  <tr key={`${c.src}>${c.dst}:${c.proto}/${c.dst_port}`}>
                    <td><span className="mono">{c.src}</span> <span className="faint small">{c.src_segment}</span></td>
                    <td><span className="mono">{c.dst}</span> <span className="faint small">{c.dst_segment}</span></td>
                    <td className="mono">{protocolName(c.proto, c.dst_port)}</td>
                    <td className="num">{formatBps(c.bps)}</td>
                    <td className="num">{c.packets.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {f && f.total.records > 0 && !device && (
        <section className="panel">
          <header><h2>Busiest ports</h2></header>
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th>Switch</th><th>Port</th><th>Average</th></tr></thead>
              <tbody>
                {f.ports.map((p) => (
                  <tr key={`${p.switch} ${p.interface}`}>
                    <td className="mono"><a href={href.switchPage(p.switch, "flows")}>{p.switch}</a></td>
                    <td className="mono">{p.interface}</td><td className="num">{formatBps(p.bps)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
