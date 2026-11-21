import { useState, type ReactNode } from "react";
import type { BgpPeer, DeviceRouting, ImportReport, PolicyNode, StaticRoute } from "./routingIntent";
import { putItem, removeItem, keyOf } from "./routingIntent";
import {
  CHOICES, NAME, PREF, addNetwork, applyChoice, choiceBlocker, choiceOf, choiceText, connect, deleteName, disconnect, isAuto,
  networkWords, prefWord, removeNetwork, setSteps, usedBy, type Choice, type Dir,
} from "./routingSimple";

// The simple routing view of a switch or pair (prototype version 6): plain
// words and choices instead of routing terms. Every change is saved at once as routing intent;
// the expert view (the page's other sections) shows the same document in full.

type Save = (next: DeviceRouting, what: string) => Promise<boolean>;

const label = (p: BgpPeer) => `${p.address}${p.vpn ? ` in ${p.vpn}` : ""}`;
const where = (vpn: string | null | undefined) => (vpn ? vpn : "Main routing table");

export function RoutingSimple({ routing, vpns, ispVpns, save, edit, imports, onImport }: {
  routing: DeviceRouting; vpns: string[]; ispVpns: string[]; save: Save; edit: boolean;
  imports: ImportReport[]; onImport: () => void;
}) {
  const peers = routing.bgp_peers ?? [];
  const isp = (p: BgpPeer) => !!p.vpn && ispVpns.includes(p.vpn);
  const attention = [
    ...peers.filter((p) => isp(p) && choiceOf(routing, p, "exp", true).id === "unset").map((p) => `${label(p)} gets everything we know: choose what we send.`),
    ...imports.filter((r) => r.error).map((r) => `${r.device} could not be read just now; the platform tries again by itself.`),
  ];
  const adopted = imports.reduce((n, r) => n + r.imported.length, 0);
  const unmanaged = imports.reduce((n, r) => n + r.unmanaged.length, 0);
  const sets = Object.keys(routing.route_policies ?? {}).filter((n) => !isAuto(n)).sort();
  const lists = Object.keys(routing.prefix_lists ?? {}).sort();

  return (
    <div className="simple">
      {attention.length > 0 && <div className="attn">{attention.map((a) => <p key={a}>! {a}</p>)}</div>}

      <section className="panel" data-tour="routing-conns">
        <header><h2>Connections to other routers <Tip word="BGP peers" /></h2></header>
        <div className="simple-list">
          {peers.length === 0 && <p className="muted">This switch has no connection to another router.</p>}
          {peers.map((p) => <Connection key={keyOf("bgp_peers", p)} routing={routing} peer={p} isp={isp(p)} save={save} edit={edit} sets={sets} lists={lists} />)}
          {edit && <NewConnection routing={routing} vpns={vpns} ispVpns={ispVpns} save={save} />}
        </div>
      </section>

      <details className="panel fold">
        <summary><h2>Network lists <Tip word="prefix lists" /></h2><span className="faint small">{lists.length} · named groups of networks</span></summary>
        <div className="simple-list">
          {lists.map((n) => <NetworkList key={n} routing={routing} name={n} save={save} edit={edit} />)}
          {edit && <NewName what="network list" routing={routing} save={save} network />}
        </div>
      </details>

      <details className="panel fold">
        <summary><h2>Rule sets <Tip word="route-policies" /></h2><span className="faint small">{sets.length} · step-by-step rules to accept, drop or prefer routes</span></summary>
        <div className="simple-list">
          {sets.map((n) => <RuleSet key={n} routing={routing} name={n} lists={lists} save={save} edit={edit} />)}
          {edit && <NewName what="rule set" routing={routing} save={save} />}
        </div>
      </details>

      <section className="panel">
        <header><h2>Fixed routes <Tip word="static routes" /></h2></header>
        <div className="simple-list">
          {(routing.static_routes ?? []).length === 0 && <p className="muted">None.</p>}
          <ul className="rfixed">
            {(routing.static_routes ?? []).map((s) => (
              <li key={keyOf("static_routes", s)}>
                <span>Traffic to <b className="mono">{s.prefix}</b>{s.next_hop === "NULL0" ? " is dropped" : <> goes to <b className="mono">{s.next_hop}</b></>}</span>
                {s.vpn && <span className="faint small">in {s.vpn}</span>}
                {edit && <button className="small" onClick={() => save(removeItem(routing, "static_routes", keyOf("static_routes", s)), `Fixed route to ${s.prefix} removed`)}>Remove</button>}
              </li>
            ))}
          </ul>
          {edit && <NewFixedRoute routing={routing} vpns={vpns} save={save} />}
        </div>
      </section>

      <section className="panel">
        <header><h2>Found on the switch</h2></header>
        <div className="simple-list">
          <p className="muted">
            {adopted ? `${adopted} routing setting${adopted > 1 ? "s" : ""} someone typed on the switch ${adopted > 1 ? "were" : "was"} added here by itself, so the platform keeps ${adopted > 1 ? "them" : "it"}.` : "Nothing typed by hand so far."}
            {unmanaged > 0 && ` ${unmanaged} ${unmanaged > 1 ? "are" : "is"} left alone: the platform cannot manage ${unmanaged > 1 ? "them" : "it"} (details in the expert view).`}
          </p>
          {edit && <button onClick={onImport}>Check the switch now</button>}
        </div>
      </section>
    </div>
  );
}

function Tip({ word }: { word: string }) {
  return <span className="tipq" tabIndex={0} title={`Engineers call these ${word}.`} aria-label={`Engineers call these ${word}.`}>?</span>;
}

function Connection({ routing, peer, isp, save, edit, sets, lists }: {
  routing: DeviceRouting; peer: BgpPeer; isp: boolean; save: Save; edit: boolean; sets: string[]; lists: string[];
}) {
  const [open, setOpen] = useState<Dir | null>(null);
  const [asking, setAsking] = useState(false);
  const line = (dir: Dir) => {
    const c = choiceOf(routing, peer, dir, isp);
    return (
      <div className={`ask${c.id === "unset" ? " warn" : ""}`}>
        <span className="q">{dir === "imp" ? "We accept" : "We send"}</span>
        <span className="a">{choiceText(c, dir)}{dir === "imp" && c.lp && c.lp !== 100 && <span className="tag">preference {prefWord(c.lp).toLowerCase()}</span>}</span>
        {edit && c.id !== "expert" && <button className={c.id === "unset" ? "primary" : ""} onClick={() => setOpen(open === dir ? null : dir)}>{c.id === "unset" ? "Choose" : "Change"}</button>}
      </div>
    );
  };
  return (
    <div className="conn">
      <div className="ch">
        <b className="mono">{peer.address}</b>{isp && <span className="tag">internet provider</span>}
        <span className="faint small">AS {peer.asn} · {where(peer.vpn)}</span>
        {edit && (asking ? (
          <span className="inline">Disconnect this router?
            <button className="danger small" onClick={() => save(disconnect(routing, peer), `Connection to ${label(peer)} removed`)}>Yes, disconnect</button>
            <button className="small" onClick={() => setAsking(false)}>No</button>
          </span>
        ) : <button className="small linkish" onClick={() => setAsking(true)}>Disconnect</button>)}
      </div>
      {line("imp")}
      {open === "imp" && <Chooser routing={routing} peer={peer} dir="imp" isp={isp} sets={sets} lists={lists} save={save} onClose={() => setOpen(null)} />}
      {line("exp")}
      {open === "exp" && <Chooser routing={routing} peer={peer} dir="exp" isp={isp} sets={sets} lists={lists} save={save} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Chooser({ routing, peer, dir, isp, sets, lists, save, onClose }: {
  routing: DeviceRouting; peer: BgpPeer; dir: Dir; isp: boolean; sets: string[]; lists: string[]; save: Save; onClose: () => void;
}) {
  const now = choiceOf(routing, peer, dir, isp);
  const [c, setC] = useState<Choice>({ id: now.id === "unset" ? "own" : now.id, list: now.list ?? lists[0], set: now.set ?? sets[0], lp: now.lp ?? 100 });
  const blocker = choiceBlocker(routing, dir, c, isp);
  const done = async () => {
    const what = `${label(peer)}: ${dir === "imp" ? "we accept" : "we send"} ${choiceText(c, dir).toLowerCase()}`;
    if (await save(applyChoice(routing, peer, dir, c), what)) onClose();
  };
  return (
    <div className="chooser">
      <div className="choices" role="radiogroup" aria-label={dir === "imp" ? "What we accept" : "What we send"}>
        {CHOICES[dir].map(([id, title, text]) => {
          const off = choiceBlocker(routing, dir, { ...c, id }, isp);
          return (
            <button key={id} role="radio" aria-checked={c.id === id} className="choice" disabled={!!off} onClick={() => setC({ ...c, id })}>
              <b>{title}</b><span>{off ?? text}</span>
            </button>
          );
        })}
      </div>
      {c.id === "list" && lists.length > 0 && (
        <label>Which list <select value={c.list} onChange={(e) => setC({ ...c, list: e.target.value })}>{lists.map((n) => <option key={n}>{n}</option>)}</select></label>
      )}
      {c.id === "set" && sets.length > 0 && (
        <label>Which rule set <select value={c.set} onChange={(e) => setC({ ...c, set: e.target.value })}>{sets.map((n) => <option key={n}>{n}</option>)}</select></label>
      )}
      {dir === "imp" && (c.id === "default" || c.id === "list") && (
        <div className="pref">
          <span className="small">Preference for these routes</span>
          <span className="toggle">{PREF.map(([v, l]) => <button key={v} aria-pressed={(c.lp ?? 100) === v} onClick={() => setC({ ...c, lp: v })}>{l}</button>)}</span>
          <span className="faint small">When two connections offer the same route, the one with more preference wins.</span>
        </div>
      )}
      {blocker && <p className="muted small">{blocker}</p>}
      <div className="actions"><button className="primary" disabled={!!blocker} onClick={done}>Save</button><button onClick={onClose}>Cancel</button></div>
    </div>
  );
}

function NewConnection({ routing, vpns, ispVpns, save }: { routing: DeviceRouting; vpns: string[]; ispVpns: string[]; save: Save }) {
  const [open, setOpen] = useState(false);
  const [toIsp, setToIsp] = useState(ispVpns.length > 0);
  const [address, setAddress] = useState("");
  const [asn, setAsn] = useState("");
  const [vpn, setVpn] = useState(ispVpns[0] ?? "");
  const [error, setError] = useState<string | null>(null);
  if (!open) return <button onClick={() => { setOpen(true); setToIsp(ispVpns.length > 0); setVpn(ispVpns[0] ?? ""); }}>+ Connect to a router</button>;
  const go = async () => {
    setError(null);
    if (!/^[0-9a-fA-F.:]+$/.test(address.trim()) || !address.includes(".") && !address.includes(":")) return setError("Write the address like 203.0.113.9.");
    if (!/^\d+$/.test(asn.trim())) return setError("The AS number is a number, like 64510: the other side gives it to you.");
    const peer: BgpPeer = { vpn: vpn || null, address: address.trim(), asn: Number(asn) };
    const next = connect(routing, peer, toIsp && ispVpns.includes(vpn));
    if (typeof next === "string") return setError(next);
    if (await save(next, `Connection to ${label(peer)}`)) setOpen(false);
  };
  return (
    <div className="chooser">
      <h3>Connect to a router</h3>
      <div className="choices two" role="radiogroup">
        {([[true, "Internet provider", "We accept only the default route at first; then choose what we send."], [false, "Another router", "A partner, a WAN or a lab router."]] as [boolean, string, string][]).map(([v, t, d]) => (
          <button key={t} role="radio" aria-checked={toIsp === v} className="choice" disabled={v && ispVpns.length === 0}
                  onClick={() => { setToIsp(v); setVpn(v ? ispVpns[0] ?? "" : ""); }}>
            <b>{t}</b><span>{v && ispVpns.length === 0 ? "Make an ISP service on the Segments page first." : d}</span>
          </button>
        ))}
      </div>
      <div className="form-row">
        <label>Its address <input className="mono" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="203.0.113.9" /></label>
        <label>Its AS number <input className="mono narrow" value={asn} onChange={(e) => setAsn(e.target.value)} placeholder="64510" inputMode="numeric" /></label>
        <label>Where on this switch
          <select value={vpn} onChange={(e) => setVpn(e.target.value)}>
            {(toIsp ? ispVpns : ["", ...vpns]).map((v) => <option key={v} value={v}>{where(v)}</option>)}
          </select>
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="actions"><button className="primary" onClick={go}>Connect</button><button onClick={() => setOpen(false)}>Cancel</button></div>
    </div>
  );
}

function NetworkList({ routing, name, save, edit }: { routing: DeviceRouting; name: string; save: Save; edit: boolean }) {
  const [net, setNet] = useState("");
  const [inside, setInside] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rules = routing.prefix_lists?.[name] ?? [];
  const used = usedBy(routing, name, label);
  const add = async () => {
    const next = addNetwork(routing, name, net, inside);
    if (typeof next === "string") return setError(next);
    setError(null);
    if (await save(next, `${name}: ${net.trim()} added`)) setNet("");
  };
  return (
    <Card title={name} sub={used.length ? `used by ${used.join(", ")}` : "not used yet"}
          action={edit && !used.length && <button className="small linkish" onClick={() => save(deleteName(routing, "prefix_lists", name), `Network list ${name} removed`)}>Delete list</button>}>
      <div className="nets">
        {rules.map((r) => (
          <span key={r.index} className="net mono">{networkWords(r)}{r.ge != null && ` (from /${r.ge})`}
            {edit && rules.length > 1 && <button aria-label={`Remove ${r.prefix}`} onClick={() => save(removeNetwork(routing, name, r.index), `${name}: ${r.prefix} removed`)}>×</button>}
          </span>
        ))}
      </div>
      {edit && (
        <div className="addrow">
          <input className="mono" value={net} onChange={(e) => setNet(e.target.value)} placeholder="add a network, e.g. 10.20.0.0/16" />
          <label className="check"><input type="checkbox" checked={inside} onChange={(e) => setInside(e.target.checked)} /> and smaller networks inside it</label>
          <button onClick={add}>Add</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </Card>
  );
}

function RuleSet({ routing, name, lists, save, edit }: { routing: DeviceRouting; name: string; lists: string[]; save: Save; edit: boolean }) {
  const steps = [...(routing.route_policies?.[name] ?? [])].sort((a, b) => a.node - b.node);
  const used = usedBy(routing, name, label);
  const write = (next: PolicyNode[], what: string) => save(setSteps(routing, name, next), `${name}: ${what}`);
  const change = (i: number, patch: Partial<PolicyNode>, what: string) => write(steps.map((s, k) => (k === i ? { ...s, ...patch } : s)), what);
  const move = (i: number, j: number) => { const next = [...steps]; [next[i], next[j]] = [next[j], next[i]]; write(next, "steps reordered"); };
  return (
    <Card title={name} sub={used.length ? `used by ${used.join(", ")}` : "not used yet"}
          action={edit && !used.length && <button className="small linkish" onClick={() => save(deleteName(routing, "route_policies", name), `Rule set ${name} removed`)}>Delete</button>}>
      <ol className="rsteps">
        {steps.map((s, i) => (
          <li key={s.node}>
            <span>If the route is</span>
            <select disabled={!edit} value={s.match_ip_prefix ?? ""} onChange={(e) => change(i, { match_ip_prefix: e.target.value || null }, `step ${i + 1}`)}>
              <option value="">any route</option>
              {lists.map((n) => <option key={n} value={n}>in {n}</option>)}
            </select>
            <span>then</span>
            <select disabled={!edit} value={s.action} onChange={(e) => change(i, { action: e.target.value as "permit" | "deny", ...(e.target.value === "deny" ? { apply_local_preference: null } : {}) }, `step ${i + 1}`)}>
              <option value="permit">accept it</option><option value="deny">drop it</option>
            </select>
            {s.action === "permit" ? (
              <select disabled={!edit} value={s.apply_local_preference ?? 100} onChange={(e) => change(i, { apply_local_preference: Number(e.target.value) === 100 ? null : Number(e.target.value) }, `step ${i + 1}`)}>
                {PREF.map(([v, l]) => <option key={v} value={v}>preference {l.toLowerCase()}</option>)}
                {s.apply_local_preference != null && !PREF.some(([v]) => v === s.apply_local_preference) && <option value={s.apply_local_preference}>local-preference {s.apply_local_preference}</option>}
              </select>
            ) : <span />}
            {edit && (
              <span className="mv">
                <button aria-label="Move up" disabled={i === 0} onClick={() => move(i, i - 1)}>↑</button>
                <button aria-label="Move down" disabled={i === steps.length - 1} onClick={() => move(i, i + 1)}>↓</button>
                {steps.length > 1 && <button aria-label="Remove step" onClick={() => write(steps.filter((_, k) => k !== i), "step removed")}>×</button>}
              </span>
            )}
          </li>
        ))}
      </ol>
      {edit && <div className="addrow"><button onClick={() => write([...steps, { node: 0, action: "permit" }], "step added")}>+ Add a step</button>
        <span className="faint small">Steps are checked from the top; the first that fits decides. A route that fits no step is dropped.</span></div>}
    </Card>
  );
}

function Card({ title, sub, action, children }: { title: string; sub: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="conn">
      <div className="ch"><b className="mono">{title}</b><span className="faint small">{sub}</span>{action}</div>
      {children}
    </div>
  );
}

function NewName({ what, routing, save, network = false }: { what: string; routing: DeviceRouting; save: Save; network?: boolean }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [net, setNet] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) return <button onClick={() => setOpen(true)}>+ New {what}</button>;
  const go = async () => {
    const n = name.trim().toUpperCase();
    if (!NAME.test(n) || routing.prefix_lists?.[n] || routing.route_policies?.[n]) return setError("Pick a new name: a letter first, then letters, digits, -, _ or .");
    const next = network ? addNetwork(routing, n, net, false) : setSteps(routing, n, [{ node: 10, action: "permit" }]);
    if (typeof next === "string") return setError(next);
    if (await save(next, `New ${what} ${n}`)) setOpen(false);
  };
  return (
    <div className="addrow">
      <input className="mono" value={name} onChange={(e) => setName(e.target.value)} placeholder={network ? "e.g. CUSTOMER-A" : "e.g. PARTNER-RULES"} aria-label="Name" />
      {network && <input className="mono" value={net} onChange={(e) => setNet(e.target.value)} placeholder="its first network, e.g. 10.20.0.0/16" aria-label="First network" />}
      <button className="primary" onClick={go}>Create</button><button onClick={() => setOpen(false)}>Cancel</button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function NewFixedRoute({ routing, vpns, save }: { routing: DeviceRouting; vpns: string[]; save: Save }) {
  const [open, setOpen] = useState(false);
  const [prefix, setPrefix] = useState("");
  const [drop, setDrop] = useState(true);
  const [hop, setHop] = useState("");
  const [vpn, setVpn] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) return <button onClick={() => setOpen(true)}>+ Add a fixed route</button>;
  const go = async () => {
    if (!/^[0-9a-fA-F.:]+\/\d{1,3}$/.test(prefix.trim())) return setError("Write the destination like 10.30.0.0/16.");
    if (!drop && !/^[0-9a-fA-F.:]+$/.test(hop.trim())) return setError("Write the address like 10.150.255.1.");
    const route: StaticRoute = { vpn: vpn || null, prefix: prefix.trim(), next_hop: drop ? "NULL0" : hop.trim() };
    if (await save(putItem(routing, "static_routes", route), `Fixed route to ${route.prefix}`)) setOpen(false);
  };
  return (
    <div className="chooser">
      <h3>New fixed route</h3>
      <div className="form-row">
        <label>Traffic to <input className="mono" value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="10.30.0.0/16" /></label>
        <label>Where on this switch <select value={vpn} onChange={(e) => setVpn(e.target.value)}>{["", ...vpns].map((v) => <option key={v} value={v}>{where(v)}</option>)}</select></label>
      </div>
      <div className="choices two" role="radiogroup">
        <button role="radio" aria-checked={drop} className="choice" onClick={() => setDrop(true)}><b>Drop it</b><span>Discard the traffic; used so a prefix can be announced.</span></button>
        <button role="radio" aria-checked={!drop} className="choice" onClick={() => setDrop(false)}><b>Send it to an address</b><span>A router reachable from this switch.</span></button>
      </div>
      {!drop && <label>Address <input className="mono" value={hop} onChange={(e) => setHop(e.target.value)} placeholder="10.150.255.1" /></label>}
      {error && <p className="error">{error}</p>}
      <div className="actions"><button className="primary" onClick={go}>Add</button><button onClick={() => setOpen(false)}>Cancel</button></div>
    </div>
  );
}

