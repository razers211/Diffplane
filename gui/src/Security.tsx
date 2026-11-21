import { useEffect, useState } from "react";
import {
  createGroup, deleteContract, deleteGroup, fetchDevices, fetchMicroseg, setContract, setEpgInside, setMicrosegMode, updateGroup,
} from "./api";
import { Failures, useSwitchState } from "./MicrosegState";
import {
  MODES, addService, arrowPath, classifierOf, ringPoints, contractOf, countersText, matrixEpgs, noContract, parseList, parseServices, servicesInput, servicesText, share,
  type Contract, type Epg, type Microseg, type MicrosegMode,
} from "./microseg";
import { draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Network › Security groups: Huawei microsegmentation. Every routed segment
// is a group (EPG); named groups take part of a segment by prefix or host MAC. A contract allows one
// group to reach another for some services; the reply is allowed with it. Off / Learn / Enforce per
// fabric. Everything here is intent: it shows in the pending bar and is deployed as usual.

const QUICK = ["any", "tcp 443", "tcp 80", "tcp 22", "icmp", "udp 53", "tcp 5432", "tcp 3306"];

// Who may reach whom: the groups on a ring, an arrow per contract. Drag a
// group onto another (or click one, then the other) to allow it in; click an arrow to change it.
function GroupMap({ epgs, contracts, chosen, disabled, onPair }: {
  epgs: Epg[]; contracts: Contract[]; chosen: { from: number; to: number } | null; disabled: boolean;
  onPair: (from: Epg, to: Epg) => void;
}) {
  useEffect(installDragDrop, []);
  const [first, setFirst] = useState<number | null>(null);
  const at = new Map(epgs.map((e, i) => [e.id, ringPoints(epgs.length, 320, 205, 150)[i]]));
  const byId = new Map(epgs.map((e) => [e.id, e]));
  useDrops({ "ms-group": (p, target) => {
    const a = byId.get(Number(p.id)), b = byId.get(Number(target));
    if (a && b && a.id !== b.id) onPair(a, b);
  } });
  // names outside the ring, so the arrows inside never cross them
  const outward = (p: { x: number; y: number }) => {
    const dx = p.x - 320, dy = p.y - 205, len = Math.hypot(dx, dy) || 1;
    const [x, y] = [(dx / len) * 44, (dy / len) * 44 + 4];
    return { x, y: y + (dy > 20 ? 6 : dy < -20 ? -2 : 0), textAnchor: (dx > 20 ? "start" : dx < -20 ? "end" : "middle") as "start" | "end" | "middle" };
  };
  const click = (e: Epg) => {
    if (disabled) return;
    if (first === null || first === e.id) return setFirst(first === e.id ? null : e.id);
    setFirst(null);
    onPair(byId.get(first)!, e);
  };
  return (
    <div className="group-map">
      <svg viewBox="0 0 640 410" role="group" aria-label="Groups and the contracts between them">
        <defs>
          <marker id="gm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="context-stroke" />
          </marker>
        </defs>
        {contracts.filter((c) => at.has(c.from) && at.has(c.to)).map((c) => {
          const on = chosen?.from === c.from && chosen?.to === c.to;
          return (
            <path key={`${c.from}-${c.to}`} className={`gm-arrow${on ? " on" : ""}`} d={arrowPath(at.get(c.from)!, at.get(c.to)!, 34)}
                  markerEnd="url(#gm-arrow)" tabIndex={disabled ? -1 : 0} role="button"
                  aria-label={`${byId.get(c.from)?.name} may reach ${byId.get(c.to)?.name}: ${servicesText(c.services)}`}
                  onClick={() => !disabled && onPair(byId.get(c.from)!, byId.get(c.to)!)}
                  onKeyDown={(ev) => { if (ev.key === "Enter") onPair(byId.get(c.from)!, byId.get(c.to)!); }}>
              <title>{`${byId.get(c.from)?.name} → ${byId.get(c.to)?.name}: ${servicesText(c.services)}`}</title>
            </path>
          );
        })}
        {epgs.map((e) => {
          const p = at.get(e.id)!;
          return (
            <g key={e.id} className={`gm-node${e.group ? " named" : ""}${first === e.id ? " first" : ""}`} transform={`translate(${p.x} ${p.y})`}
               tabIndex={disabled ? -1 : 0} role="button" aria-label={`Group ${e.name}`} onClick={() => click(e)}
               onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); click(e); } }}
               {...(disabled ? {} : { ...draggable({ type: "epg", label: e.name ?? `EPG ${e.id}`, id: e.id }), "data-no-pick": "", ...dropZone("ms-group", "epg", String(e.id)) })}>
              <circle r={30} />
              <text className="id" y={4}>{e.id}</text>
              <text className="name" {...outward(p)}>{e.group ? `↳ ${e.name}` : e.name}</text>
            </g>
          );
        })}
      </svg>
      <p className="faint small">{first !== null ? `Now click the group ${byId.get(first)?.name} may reach.` : "Drag a group onto the one it may reach (or click both); click an arrow to change it. Replies are allowed with it."}</p>
    </div>
  );
}

const message = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));

interface GroupForm { id: number | null; segment: number; name: string; prefixes: string; macs: string; deny: boolean }

export function Security({ site }: { site: string }) {
  const canOperate = useCan("operator");
  const view = useFetch(() => (site ? fetchMicroseg(site) : Promise.resolve(null)), [site]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cell, setCell] = useState<{ from: number; to: number } | null>(null);
  const [services, setServices] = useState("");
  const [group, setGroup] = useState<GroupForm | null>(null);
  const devices = useFetch(() => (site ? fetchDevices(site) : Promise.resolve([])), [site]);
  const [device, setDevice] = useState("");
  const live = useSwitchState();

  if (!site) return <p className="empty muted">Pick a fabric (top right): security groups belong to one fabric.</p>;
  if (view.error) return <p className="error">{view.error}</p>;
  const data: Microseg | null = view.data;
  if (!data) return <p className="muted">Loading…</p>;

  const act = async (work: () => Promise<unknown>, done: string) => {
    setError(null);
    setNotice(null);
    try {
      await work();
      setNotice(`${done} It reaches the switches at the next deploy.`);
      view.reload();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    }
  };

  const epgs = matrixEpgs(data.epgs);
  const byId = new Map(data.epgs.map((e) => [e.id, e]));
  const segments = epgs.filter((e) => e.group === null);
  const inactive = data.epgs.filter((e) => !e.active);
  const label = (id: number) => byId.get(id)?.name ?? `EPG ${id}`;

  const openCell = (from: Epg, to: Epg) => {
    if (from.id === to.id) return;
    setCell({ from: from.id, to: to.id });
    const c = contractOf(data.contracts, from.id, to.id);
    setServices(c ? servicesInput(c.services) : "");
  };
  const saveContract = async () => {
    if (!cell) return;
    const parsed = parseServices(services);
    if (typeof parsed === "string") return setError(parsed);
    if (await act(() => setContract(site, cell.from, cell.to, parsed), `${label(cell.from)} may reach ${label(cell.to)}: ${servicesText(parsed)}.`)) setCell(null);
  };
  const removeContract = async () => {
    if (!cell) return;
    if (await act(() => deleteContract(site, cell.from, cell.to), `Contract ${label(cell.from)} → ${label(cell.to)} removed.`)) setCell(null);
  };
  const saveGroup = async () => {
    if (!group) return;
    const body = { segment: group.segment, name: group.name.trim(), prefixes: parseList(group.prefixes), macs: parseList(group.macs),
                   inside: group.deny ? "deny" as const : null };
    const ok = await act(() => (group.id === null ? createGroup(site, body) : updateGroup(site, group.id, body)),
                         `Group ${body.name} saved.`);
    if (ok) setGroup(null);
  };

  return (
    <div className="security-page">
      <section className="panel" data-tour="security-mode">
        <header>
          <h2>Microsegmentation</h2>
          <span className="hint">Huawei EPGs and contracts · V300R025 or later on every leaf</span>
        </header>
        <div className="mode-pills" role="radiogroup" aria-label="Microsegmentation mode">
          {MODES.map((m) => (
            <button key={m.mode} role="radio" aria-checked={data.mode === m.mode} className={data.mode === m.mode ? "pill-on" : ""}
              disabled={!canOperate || data.mode === m.mode}
              onClick={() => act(() => setMicrosegMode(site, m.mode as MicrosegMode), `Mode ${m.label}.`)}>
              {m.label}
            </button>
          ))}
          <span className="muted">{MODES.find((m) => m.mode === data.mode)?.text}</span>
        </div>
        <div className="usage" data-tour="security-usage">
          {(["epgs", "members", "rules"] as const).map((k) => (
            <div className="load" key={k} title={`${data.usage[k]} of ${data.usage.limits[k]} per chip`}>
              <span className="small">{k === "epgs" ? "Groups" : k === "members" ? "Members" : "Rules"}</span>
              <span className="track"><i style={{ width: `${share(data.usage[k], data.usage.limits[k])}%` }} /></span>
              <span className="mono small">{data.usage[k]} / {data.usage.limits[k]}</span>
            </div>
          ))}
          <span className="faint small">The most one switch holds (a border leaf or gateway has every group).</span>
        </div>
        {error && <p className="error">{error}</p>}
        {notice && <p className="notice">{notice}</p>}
      </section>

      <section className="panel" data-tour="security-groups">
        <header>
          <h2>Groups</h2>
          <span className="hint">Every routed segment is a group; a named group takes part of it</span>
        </header>
        {segments.length === 0 && <p className="empty muted">No routed segment yet: route a segment on the Segments page first.</p>}
        <table className="devices">
          <thead><tr><th>ID</th><th>Group</th><th>Members</th><th>Inside the group</th><th /></tr></thead>
          <tbody>
            {epgs.map((e) => (
              <tr key={e.id} className={e.group ? "sub" : ""}>
                <td className="mono">{e.id}</td>
                <td>{e.group ? <span className="mono">↳ {e.name}</span> : <strong className="mono">{e.name}</strong>}</td>
                <td className="mono small">{[...e.prefixes, ...e.macs].join(", ")}</td>
                <td>
                  <button className="linkish" disabled={!canOperate} title="Traffic between hosts of this group"
                    onClick={() => act(() => setEpgInside(site, e.id, e.inside === "deny" ? "permit" : "deny"),
                                       `${e.name}: inside ${e.inside === "deny" ? "allowed" : "denied"}.`)}>
                    <span className={e.inside === "deny" ? "pill bad" : "pill good"}>{e.inside === "deny" ? "denied" : "allowed"}</span>
                  </button>
                </td>
                <td>
                  {canOperate && e.group === null && (
                    <button onClick={() => setGroup({ id: null, segment: e.segment, name: "", prefixes: "", macs: "", deny: false })}>Add group</button>
                  )}
                  {canOperate && e.group !== null && (
                    <>
                      <button onClick={() => setGroup({ id: e.id, segment: e.segment, name: e.group!, prefixes: e.prefixes.join(", "),
                                                        macs: e.macs.join(", "), deny: e.inside === "deny" })}>Edit</button>
                      <button onClick={() => act(() => deleteGroup(site, e.id), `Group ${e.name} deleted.`)}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {group && (
          <div className="form-row">
            <label htmlFor="g-name">Group name (in {segments.find((s) => s.segment === group.segment)?.name})
              <input id="g-name" value={group.name} onChange={(ev) => setGroup({ ...group, name: ev.target.value })} placeholder="admins" />
            </label>
            <label htmlFor="g-prefixes">Prefixes inside the segment
              <input id="g-prefixes" value={group.prefixes} onChange={(ev) => setGroup({ ...group, prefixes: ev.target.value })} placeholder="10.100.0.16/28" spellCheck={false} />
            </label>
            <label htmlFor="g-macs">Host MACs (bridged traffic)
              <input id="g-macs" value={group.macs} onChange={(ev) => setGroup({ ...group, macs: ev.target.value })} placeholder="0200-fc00-0001" spellCheck={false} />
            </label>
            <label className="check" htmlFor="g-deny">
              <input id="g-deny" type="checkbox" checked={group.deny} onChange={(ev) => setGroup({ ...group, deny: ev.target.checked })} />
              Deny between its own hosts
            </label>
            <button className="primary" onClick={saveGroup}>Save group</button>
            <button onClick={() => setGroup(null)}>Cancel</button>
          </div>
        )}
        {inactive.length > 0 && (
          <p className="faint small">Kept for later, inactive while their segment is not routed: {inactive.map((e) => `EPG ${e.id}`).join(", ")}.</p>
        )}
      </section>

      {epgs.length > 1 && (
        <section className="panel" data-tour="security-matrix">
          <header>
            <h2>Contracts</h2>
            <span className="hint">Rows reach columns · click a cell to allow services · replies are allowed with them</span>
          </header>
          <div className="form-row" data-tour="security-counters">
            <label htmlFor="c-switch">Counters from
              <select id="c-switch" value={device} onChange={(ev) => setDevice(ev.target.value)}>
                <option value="">a switch…</option>
                {(devices.data ?? []).filter((d) => d.status === "active" && d.role !== "spine").map((d) => (
                  <option key={d.name} value={d.name}>{d.name}</option>
                ))}
              </select>
            </label>
            <button disabled={!device || live.busy} onClick={() => live.read(device)}>{live.busy ? "Reading…" : "Read now"}</button>
            {live.state && <span className="hint">read from {live.state.device} · packets since the contract was deployed</span>}
            {live.error && <p className="error">{live.error}</p>}
          </div>
          {live.state && <Failures state={live.state} />}
          <GroupMap epgs={epgs} contracts={data.contracts} chosen={cell} disabled={!canOperate} onPair={openCell} />
          {cell && (
            <div className="form-row">
              <label htmlFor="c-services">{label(cell.from)} may reach {label(cell.to)} on
                <input id="c-services" value={services} onChange={(ev) => setServices(ev.target.value)}
                  placeholder="tcp 5432, tcp 8000-8080, icmp — or any" spellCheck={false} autoFocus />
              </label>
              <span className="quick-picks" aria-label="Quick picks">
                {QUICK.map((q) => <button key={q} type="button" className="small" onClick={() => setServices(addService(services, q))}>+ {q}</button>)}
              </span>
              <button className="primary" onClick={saveContract}>Save contract</button>
              {contractOf(data.contracts, cell.from, cell.to) && <button onClick={removeContract}>Remove contract</button>}
              <button onClick={() => setCell(null)}>Cancel</button>
            </div>
          )}
          <div className="matrix-scroll">
            <table className="matrix">
              <thead>
                <tr><th>from \ to</th>{epgs.map((t) => <th key={t.id} className="mono">{t.name}</th>)}</tr>
              </thead>
              <tbody>
                {epgs.map((f) => (
                  <tr key={f.id}>
                    <th className="mono">{f.name}</th>
                    {epgs.map((t) => {
                      const c = contractOf(data.contracts, f.id, t.id);
                      const chosen = cell?.from === f.id && cell?.to === t.id;
                      return (
                        <td key={t.id} className={`${c ? "allowed" : f.id === t.id ? "self" : data.mode === "enforce" ? "denied" : "open"}${chosen ? " chosen" : ""}`}>
                          {f.id === t.id ? <span className="faint small">{noContract(data.mode, f, t)}</span> : (
                            <button className="cell" disabled={!canOperate} onClick={() => openCell(f, t)}>
                              {c ? servicesText(c.services) : <span className="faint">{noContract(data.mode, f, t)}</span>}
                              {c && live.state && <span className="counters small">{countersText(live.state.contracts[classifierOf(c)])}</span>}
                            </button>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="faint small">Hosts in no group (the outside, through firewalls and ISPs) are always allowed: the firewall filters them.</p>
        </section>
      )}
    </div>
  );
}
