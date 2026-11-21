import { useState } from "react";
import type { Service } from "./api";
import { draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";
import type { BgpPeer, DeviceRouting } from "./routingIntent";
import { keyOf } from "./routingIntent";
import { gateRules, isAuto, setGate, type Dir } from "./routingSimple";

// The expert view's route flow (prototype version 6): each BGP peer's routes come in from the
// left through its import filter into the switch and leave to the right through its export filter.
// A route-policy or prefix list dragged onto a filter becomes that direction's filter. Sessions the
// platform derives from a firewall service are drawn dashed (not routing intent, not editable here).

type Save = (next: DeviceRouting, what: string) => Promise<boolean>;

interface Row { peer: BgpPeer; isp: boolean; service: boolean }

export function RouteFlow({ routing, devices, vpns, ispVpns, services, save, edit }: {
  routing: DeviceRouting; devices: string[]; vpns: string[]; ispVpns: string[]; services: Service[]; save: Save; edit: boolean;
}) {
  installDragDrop();
  const [open, setOpen] = useState<string | null>(null);
  const rows: Row[] = [
    ...(routing.bgp_peers ?? []).map((peer) => ({ peer, isp: !!peer.vpn && ispVpns.includes(peer.vpn), service: false })),
    ...services.filter((s) => s.kind === "firewall" && s.asn != null && (s.devices ?? [s.device]).some((d) => devices.includes(d)))
      .map((s) => ({ peer: { vpn: s.name, address: s.next_hop, asn: s.asn! }, isp: false, service: true })),
  ];
  useDrops({
    "rt-gate": (p, target) => {
      const [key, dir] = target.split("|") as [string, Dir];
      const peer = (routing.bgp_peers ?? []).find((x) => keyOf("bgp_peers", x) === key);
      if (peer) save(setGate(routing, peer, dir, p.type as "policy" | "plist", p.label), `${peer.address} ${dir === "imp" ? "import" : "export"}: ${p.label}`);
    },
  });
  const own = [
    ...(routing.static_routes ?? []).map((s) => ["static", `${s.prefix} → ${s.next_hop}`, s.vpn]),
    ...(routing.aggregates ?? []).map((a) => ["aggregate", `${a.prefix}${a.detail_suppressed ? " (summary only)" : ""}`, a.vpn]),
    ...(routing.ospf ?? []).map((o) => ["OSPF", `process ${o.process}`, o.vpn]),
  ] as [string, string, string | null | undefined][];
  const policies = Object.keys(routing.route_policies ?? {}).sort();
  const lists = Object.keys(routing.prefix_lists ?? {}).sort();
  const pipe = (dir: string, r: Row) => <div className={`pipe ${dir}${r.service ? " dash" : ""}${dir === "out" && r.isp && !r.peer.export_policy && !r.peer.export_prefix_list ? " stop" : ""}`} aria-hidden="true"><i /></div>;
  const peerBox = (r: Row, side: string) => (
    <div className={`peerbox ${side}${r.isp ? " isp" : ""}${r.service ? " svc" : ""}`}>
      <div className="pn"><b className="mono">{r.peer.address}</b>{r.isp && <span className="tag">ISP</span>}{r.service && <span className="tag grey">service</span>}</div>
      <span className="pm">AS {r.peer.asn} · {r.peer.vpn ?? "global"}</span>
    </div>
  );
  const gate = (r: Row, dir: Dir, i: number) => {
    if (r.service) return <div className="gatebox svcgate"><div className="gh"><b>set by the service</b></div><ul className="mini"><li className="ok">{dir === "imp" ? "its routes" : "segment subnets"}</li></ul></div>;
    const name = [r.peer[dir === "imp" ? "import_policy" : "export_policy"], r.peer[dir === "imp" ? "import_prefix_list" : "export_prefix_list"]].filter(Boolean).join(" + ");
    const warn = dir === "exp" && r.isp && !name;
    const id = `${i}|${dir}`;
    return (
      <div className={`gatebox${name ? "" : " open"}${warn ? " warn" : ""}${open === id ? " sel" : ""}`} role="button" tabIndex={0}
           aria-label={`${dir === "imp" ? "Import" : "Export"} filter of ${r.peer.address}: ${name || "none"}`}
           onClick={() => setOpen(open === id ? null : id)} onKeyDown={(e) => e.key === " " && setOpen(open === id ? null : id)}
           {...(edit ? dropZone("rt-gate", "policy plist", `${keyOf("bgp_peers", r.peer)}|${dir}`) : {})} data-no-pick>
        <div className="gh">
          <span className="funnel" aria-hidden="true">⏷</span><b>{name || (warn ? "no policy" : "no filter")}</b>
          {edit && name && <button className="x" aria-label="Remove filter" onClick={(e) => { e.stopPropagation(); save(setGate(routing, r.peer, dir, null, null), `${r.peer.address} ${dir === "imp" ? "import" : "export"}: no filter`); }}>×</button>}
        </div>
        <ul className="mini">
          {warn ? <li className="no">everything we know goes out</li>
            : gateRules(routing, r.peer, dir).map((g, k) => <li key={k} className={g.ok ? "ok" : "no"}>{g.text}{g.lp != null && <span className="lp">lp {g.lp}</span>}</li>)}
        </ul>
      </div>
    );
  };
  const opened = open && rows[Number(open.split("|")[0])];
  const openedPolicy = opened && !opened.service ? opened.peer[open!.endsWith("imp") ? "import_policy" : "export_policy"] : null;

  return (
    <section className="panel" data-tour="route-flow">
      <header><h2>Route flow</h2><span className="hint">routes come in from the left, leave to the right</span></header>
      {edit && (
        <div className="blocks">
          <span className="eyebrow">Drag onto a filter</span>
          {policies.map((n) => <span key={n} className="chip pol-chip" tabIndex={0} {...draggable({ type: "policy", label: n })}>{n}{isAuto(n) && <span className="faint"> · simple view</span>}</span>)}
          {lists.map((n) => <span key={n} className="chip pl-chip" tabIndex={0} {...draggable({ type: "plist", label: n })}>{n}</span>)}
          {!policies.length && !lists.length && <span className="faint small">No route-policy or prefix list yet (below).</span>}
        </div>
      )}
      <figure className="flowfig">
        <div className="flowmap" role="img" aria-label={`Route flow of ${devices.join(" and ")}: ${rows.map((r) => r.peer.address).join(", ") || "no BGP peer"}`}>
          {["From", "Filter in", "", "Filter out", "To"].map((h, c) => <div key={c} className="colh" style={{ gridColumn: c * 2 + 1 }}>{h}</div>)}
          <div className="swbox" style={{ gridRow: `2 / span ${Math.max(rows.length, 1)}` }}>
            <div className="swn"><b>{devices.length > 1 ? "This pair" : devices[0]}</b>{devices.length > 1 && <span className="mono">{devices.join(" · ")}</span>}</div>
            <span className="eyebrow">VPN instances</span>
            <div className="row">{vpns.length ? vpns.map((v) => <span key={v} className="kchip">{v}</span>) : <span className="faint small">global table only</span>}</div>
            <span className="eyebrow">Its own routes</span>
            {own.length ? <ul className="own">{own.map(([k, t, v], i) => <li key={i}><span className="k">{k}</span><span className="mono">{t}</span><span className="v">{v ?? "global"}</span></li>)}</ul>
              : <span className="faint small">none</span>}
          </div>
          {!rows.length && <div className="nopeer" style={{ gridRow: 2, gridColumn: "1 / 4" }}>No BGP peer on this switch.</div>}
          {rows.map((r, i) => [peerBox(r, "in"), pipe("in", r), gate(r, "imp", i), pipe("in", r), pipe("out", r), gate(r, "exp", i), pipe("out", r), peerBox(r, "out")]
            .map((el, k) => <div key={`${i}-${k}`} className="cell" style={{ gridRow: i + 2, gridColumn: [1, 2, 3, 4, 6, 7, 8, 9][k], ["--o" as string]: i * 10 }}>{el}</div>))}
        </div>
        <figcaption>
          Click a filter to read its rules. Green passes, red is dropped; the first rule that matches decides. Dashed: a session the platform derives from a firewall service.
          {edit && " Drag a route-policy or a prefix list onto a filter to use it there."}
        </figcaption>
      </figure>
      {opened && !opened.service && (
        <div className="gate-detail">
          <b>{opened.peer.address} · {open!.endsWith("imp") ? "what we accept" : "what we send"}</b>
          {openedPolicy ? (
            <ol className="rules">
              {[...(routing.route_policies?.[openedPolicy] ?? [])].sort((a, b) => a.node - b.node).map((n) => (
                <li key={n.node} className={n.action === "permit" ? "ok" : "no"}>
                  <span className="mark">{n.action === "permit" ? "✓" : "✕"}</span>
                  <span>{n.action === "permit" ? "Accept" : "Drop"} {n.match_ip_prefix ? `the networks in ${n.match_ip_prefix}` : n.action === "deny" ? "everything else" : "any route"}
                    {n.apply_local_preference != null && `, with local-preference ${n.apply_local_preference}`}.</span>
                  <span className="nodeno">node {n.node}</span>
                </li>
              ))}
            </ol>
          ) : <ul className="mini">{gateRules(routing, opened.peer, open!.endsWith("imp") ? "imp" : "exp").map((g, k) => <li key={k} className={g.ok ? "ok" : "no"}>{g.text}</li>)}</ul>}
        </div>
      )}
    </section>
  );
}
