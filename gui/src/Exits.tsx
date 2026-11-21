import { useEffect, useState } from "react";
import { setRouting, type Segment, type Service } from "./api";
import { draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";
import { doorOf, routingBody } from "./exits";
import { colourOf } from "./ports";
import { useCan } from "./role";

// Network › Segments, "Where traffic leaves": drop a segment on a firewall to
// take its default route there, on "No default route" to drop it, on another segment to peer them.
// A segment not routed yet is routed by the drop (a gateway subnet from the pool), after a question.

const message = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));

export function Exits({ site, segments, services, onDone }: {
  site: string; segments: Segment[]; services: Service[]; onDone: (text: string) => void;
}) {
  useEffect(installDragDrop, []);
  const canOperate = useCan("operator");
  const [ask, setAsk] = useState<{ segment: Segment; firewall: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const firewalls = services.filter((s) => s.kind !== "isp");
  const byVni = new Map(segments.map((s) => [s.vni, s]));

  const write = async (s: Segment, change: { firewall?: string | null; peers?: number[] }, text: string) => {
    setError(null);
    try {
      await setRouting(site, s.vni, routingBody(s, change));
      onDone(text);
    } catch (e) {
      setError(message(e));
    }
  };
  const toDoor = (s: Segment, firewall: string | null) => {
    if (!s.routing) return setAsk({ segment: s, firewall });
    if (doorOf(s) === firewall) return;
    write(s, { firewall }, firewall ? `${s.name} now leaves through ${firewall}.` : `${s.name} has no default route now.`);
  };
  const peer = (a: Segment, b: Segment) => {
    if (a.vni === b.vni) return;
    if (!a.routing || !b.routing) return setError(`Peering needs both segments routed: ${!a.routing ? a.name : b.name} is not.`);
    if (a.routing.peers.includes(b.vni)) return setError(`${a.name} and ${b.name} are peered already.`);
    write(a, { peers: [...a.routing.peers, b.vni] }, `${a.name} and ${b.name} now reach each other.`);
  };
  useDrops({
    "exit-door": (p, target) => { const s = byVni.get(Number(p.vni)); if (s) toDoor(s, target || null); },
    "exit-seg": (p, target) => { const a = byVni.get(Number(p.vni)), b = byVni.get(Number(target)); if (a && b) peer(a, b); },
  });

  const card = (s: Segment) => (
    <div key={s.vni} className={`exit-seg s${colourOf(segments, s.vni)}`} tabIndex={0} role="group" aria-label={`Segment ${s.name}`}
         {...(canOperate ? dropZone("exit-seg", "exitseg", String(s.vni)) : {})}>
      <button className="exit-chip" disabled={!canOperate} {...(canOperate ? draggable({ type: "exitseg", label: s.name, vni: s.vni }) : {})}>
        <i />{s.name}
      </button>
      {s.routing && <span className="mono small faint">{s.routing.gateway}</span>}
      {(s.routing?.peers ?? []).map((v) => (
        <span key={v} className="peer-tag">↔ {byVni.get(v)?.name ?? `VNI ${v}`}
          {canOperate && (
            <button className="linkish" aria-label={`Stop peering ${s.name} with ${byVni.get(v)?.name}`}
                    onClick={() => write(s, { peers: s.routing!.peers.filter((x) => x !== v) }, `${s.name} and ${byVni.get(v)?.name} no longer peered.`)}>×</button>
          )}
        </span>
      ))}
    </div>
  );
  const door = (key: string, title: string, sub: string, members: Segment[], firewall: string | null | undefined) => (
    <div key={key} className={`exit-door${firewall === undefined ? " idle" : ""}`} tabIndex={firewall === undefined ? -1 : 0} role="group" aria-label={title}
         {...(canOperate && firewall !== undefined ? dropZone("exit-door", "exitseg", firewall ?? "") : {})}>
      <header><b>{title}</b><span className="faint small">{sub}</span></header>
      <div className="exit-members">{members.map(card)}{members.length === 0 && <span className="faint small">{firewall === undefined ? "—" : "Drop a segment here"}</span>}</div>
    </div>
  );

  return (
    <section className="panel exits" data-tour="exits">
      <header>
        <h2>Where traffic leaves</h2>
        <span className="hint">{canOperate ? "Drag a segment onto a firewall, or onto another segment to peer them" : "Read only"}</span>
      </header>
      <div className="exit-doors">
        {firewalls.map((f) => door(f.name, f.name, `firewall · ${f.devices?.join(" + ") ?? f.device}`, segments.filter((s) => doorOf(s) === f.name), f.name))}
        {door("none", "No default route", "routed, reaches only its peers", segments.filter((s) => doorOf(s) === null), null)}
        {door("unrouted", "Not routed", "layer 2 only", segments.filter((s) => doorOf(s) === undefined), undefined)}
      </div>
      {ask && (
        <div className="bundle-bar">
          <span><b>{ask.segment.name}</b> is not routed. Route it{ask.firewall ? <> through <b>{ask.firewall}</b></> : " with no default route"}? It gets a /24 gateway subnet from the pool.</span>
          <button className="primary" onClick={() => { const a = ask; setAsk(null); write(a.segment, { firewall: a.firewall }, `${a.segment.name} routed${a.firewall ? ` through ${a.firewall}` : ""}.`); }}>Route it</button>
          <button onClick={() => setAsk(null)}>Cancel</button>
        </div>
      )}
      {error && <p className="error pad">{error}</p>}
    </section>
  );
}
