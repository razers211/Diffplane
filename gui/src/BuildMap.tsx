import { useEffect, useState } from "react";
import { fetchRemoval, fetchTopology, removeDevice, type BuildOptions } from "./api";
import { useCan } from "./role";
import { GENERIC, type Kind, type LinkDraft, type SwitchDraft } from "./buildForm";
import { useFetch } from "./useFetch";
import { draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";

// Adding switches on the map: place new switches by clicking or dragging
// a tile, cable them by clicking a spine and a leaf. Names and ports are proposed; everything is
// still editable in the cards below the map, and checked together before anything is written.
// tiles dragged with pointer events (touch too), a new switch cabled to
// every spine or leaf for you, a switch dragged onto another to cable them, an inventory switch
// dragged onto a new box to make it that switch.

const C = Math.cos(Math.PI / 6), S = 0.5;
const W = 1, D = 1, H = 0.45;
const SCALE = 34;

interface Box { name: string; c: number; r: number; fresh: boolean; spine: boolean }

const TILES: { kind: Kind; label: string; sub: string }[] = [
  { kind: "pair", label: "Leaf pair", sub: "two leaves in M-LAG" },
  { kind: "leaf", label: "Lone leaf", sub: "no M-LAG peer" },
  { kind: "spine", label: "Spine", sub: "route reflector" },
  { kind: "spinepair", label: "Spine pair", sub: "M-LAG, may be the gateway pair" },
];

/** Columns for a row of switches: pairs side by side, a wider gap between groups, centred. */
function row(groups: { names: string[]; fresh: boolean }[], r: number, spine: boolean): Box[] {
  const boxes: Box[] = [];
  let c = 0;
  for (const g of groups) {
    g.names.forEach((name, i) => boxes.push({ name, c: c + i * 1.25, r, fresh: g.fresh, spine }));
    c += (g.names.length - 1) * 1.25 + 1.6;
  }
  const mid = (c - 1.6) / 2;
  return boxes.map((b) => ({ ...b, c: b.c - mid }));
}

export function BuildMap({ site, opts, drafts, links, model, onModel, onAdd, onLink, onStock, onDrop, onRemoved }: {
  site: string; opts: BuildOptions; drafts: SwitchDraft[]; links: LinkDraft[];
  model: string; onModel: (model: string) => void;   // the model new switches get (GENERIC: detected later)
  onAdd: (kind: Kind) => void;
  onLink: (spine: string, leaf: string) => string | null;   // why not, or null when cabled
  onStock: (name: string, serial: string) => string | null; // an inventory switch onto a new box
  onDrop: (name: string) => void;      // take a switch (with its pair) out of the batch
  onRemoved?: () => void;              // a planned switch was removed from the fabric
}) {
  const topology = useFetch(() => (opts.fresh ? Promise.resolve(null) : fetchTopology(site)), [site, opts.fresh]);
  const canOperate = useCan("operator");
  const [pick, setPick] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [removal, setRemoval] = useState<{ devices: string[]; links: string[]; unpair: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const kinds = TILES.filter((t) => t.kind !== "spinepair" || opts.centralized);
  useEffect(installDragDrop, []);

  // existing switches: pairs grouped when both are paired and named one after the other
  const existingLeaves = opts.devices.filter((d) => d.role !== "spine").map((d) => d.name).sort(natural);
  const existingSpines = opts.devices.filter((d) => d.role === "spine").map((d) => d.name).sort(natural);
  const paired = new Set(opts.devices.filter((d) => d.paired).map((d) => d.name));
  const leafGroups: { names: string[]; fresh: boolean }[] = [];
  for (let i = 0; i < existingLeaves.length; i++) {
    const a = existingLeaves[i], b = existingLeaves[i + 1];
    if (paired.has(a) && b && paired.has(b)) { leafGroups.push({ names: [a, b], fresh: false }); i++; }
    else leafGroups.push({ names: [a], fresh: false });
  }
  const spineGroups = existingSpines.map((n) => ({ names: [n], fresh: false }));
  for (const d of drafts) {
    const names = d.names.map((n) => n.trim()).filter((n) => n && !opts.devices.some((x) => x.name === n));
    if (!names.length) continue;
    (d.kind === "spine" || d.kind === "spinepair" ? spineGroups : leafGroups).push({ names, fresh: true });
  }
  const boxes = [...row(spineGroups, 0, true), ...row(leafGroups, 4, false)];
  const at = new Map(boxes.map((b) => [b.name, b]));
  const span = Math.max(4, ...boxes.map((b) => Math.abs(b.c) + 1.5));
  const scale = Math.min(SCALE, 300 / (span * 2 * C));
  const ox = 360, oy = 40;
  const p = (x: number, y: number, z: number) => [ox + (x - y) * C * scale, oy + (x + y) * S * scale - z * scale];
  const grid = (b: Box) => [b.c + b.r, b.r - b.c];
  const top = (b: Box) => { const [x, y] = grid(b); return p(x + W / 2, y + D / 2, H); };
  
  const old = (topology.data?.links ?? [])
    .map((l) => [l.ends[0]?.device, l.ends[1]?.device])
    .filter(([a, b]) => a && b && at.has(a) && at.has(b) && at.get(a)!.spine !== at.get(b)!.spine) as [string, string][];
  const fresh = links.filter((l) => at.has(l.spine) && at.has(l.leaf));

  const picked = pick ? at.get(pick) ?? null : null;
  const status = (name: string) => topology.data?.nodes.find((n) => n.name === name)?.status ?? null;
  const askRemoval = async (name: string) => {
    setSaid(null);
    try {
      setRemoval(await fetchRemoval(name));
    } catch (e) {
      setSaid(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    }
  };
  const remove = async (name: string) => {
    setBusy(true);
    try {
      const r = await removeDevice(name);
      setSaid(null);
      setRemoval(null);
      setPick(null);
      topology.reload();
      onRemoved?.();
      setDone(`Removed ${r.devices.join(" and ")} from ${site}.`);
    } catch (e) {
      setSaid(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    } finally {
      setBusy(false);
    }
  };
  const connect = (a: Box, b: Box) => {
    if (a.name === b.name) return;
    if (a.spine === b.spine) return setSaid("A cable runs between a spine and a leaf.");
    if (!a.fresh && !b.fresh) return setSaid("At least one end of a new cable must be a new switch.");
    const [spine, leaf] = a.spine ? [a.name, b.name] : [b.name, a.name];
    if (links.some((l) => l.spine === spine && l.leaf === leaf)) return setSaid(`${spine} and ${leaf} are already cabled here.`);
    setSaid(onLink(spine, leaf));
  };
  const click = (b: Box) => {
    setSaid(null);
    setRemoval(null);
    setDone(null);
    if (!pick) return setPick(b.name);
    if (pick === b.name) return setPick(null);
    setPick(null);
    connect(at.get(pick)!, b);
  };
  const generic = new Set(drafts.filter((d) => d.model === GENERIC).flatMap((d) => d.names.map((n) => n.trim())));
  const stocked = new Map(drafts.flatMap((d) => d.names.map((n, i) => [n.trim(), d.inventory[i]] as const)).filter(([, s]) => s));
  useDrops({
    "bm-map": (p) => { if (kinds.some((t) => t.kind === p.kind)) onAdd(p.kind as Kind); },
    "bm-box": (p, target) => {
      setDone(null);
      const b = at.get(target);
      if (!b) return;
      if (p.type === "box") { const a = at.get(String(p.name)); if (a) { setSaid(null); connect(a, b); } }
      else setSaid(onStock(target, String(p.serial)));
    },
  });

  return (
    <div className="build-map">
      <div className="build-canvas" {...dropZone("bm-map", "kind", "")}>
        <svg viewBox="0 0 720 300" role="group" aria-label="The fabric with the switches being added">
          {[...old.map(([a, b]) => ({ a, b, cls: "old" })), ...fresh.map((l) => ({ a: l.spine, b: l.leaf, cls: "new" }))].map(({ a, b, cls }, i) => {
            const s = top(at.get(a)!), e = top(at.get(b)!);
            return <line key={`${cls}${i}${a}${b}`} className={`bm-link ${cls}`} x1={s[0]} y1={s[1]} x2={e[0]} y2={e[1]} />;
          })}
          {boxes.map((b) => {
            const [x, y] = grid(b);
            return (
              <g key={b.name} className={`bm-node${b.fresh ? " fresh" : ""}${pick === b.name ? " picked" : ""}`} tabIndex={0} role="button"
                {...draggable({ type: "box", label: `Cable from ${b.name}`, name: b.name })} data-no-pick=""
                {...dropZone("bm-box", b.fresh ? "box inv" : "box", b.name)}
                aria-label={`${b.name}${b.fresh ? " (new)" : ""}${stocked.get(b.name) ? `, inventory switch ${stocked.get(b.name)}` : ""}${generic.has(b.name) ? ", generic: detect it" : ""}`} onClick={() => click(b)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); click(b); } }}>
                {(() => {
                  const [cx, cy] = top(b), bw = C * scale * 1.5, bh = S * scale * 1.5 + 6;
                  return (<>
                    <rect className="t" x={cx - bw / 2} y={cy - bh / 2} width={bw} height={bh} rx={4} />
                    <rect className="st" x={cx - bw / 2} y={cy - bh / 2} width={3.5} height={bh} rx={1.5} />
                  </>);
                })()}
                <text x={p(x + W, y + D, 0)[0]} y={p(x + W, y + D, 0)[1] + 14} textAnchor="middle">{b.name}</text>
                {generic.has(b.name) && <text className="bm-serial" x={p(x + W, y + D, 0)[0]} y={p(x + W, y + D, 0)[1] + 26} textAnchor="middle">detect its ports</text>}
                {stocked.get(b.name) && <text className="bm-serial" x={p(x + W, y + D, 0)[0]} y={p(x + W, y + D, 0)[1] + 26} textAnchor="middle">{stocked.get(b.name)}</text>}
              </g>
            );
          })}
          {boxes.length === 0 && <text x="360" y="150" textAnchor="middle" className="bm-empty">Place the first switches: click or drag a tile on the right</text>}
        </svg>
      </div>
      <div className="build-side">
        <label className="small">New switches are
          <select value={model} onChange={(e) => onModel(e.target.value)}>
            {Object.keys(opts.models).map((m) => <option key={m} value={m}>{m}</option>)}
            <option value={GENERIC}>Generic: detect its ports</option>
          </select>
        </label>
        <div className="bm-tiles">
          {kinds.map((t) => (
            <button key={t.kind} type="button" className="bm-tile" onClick={() => onAdd(t.kind)}
              {...draggable({ type: "kind", label: `+ ${t.label}`, kind: t.kind })} data-no-pick="">
              <b>+ {t.label}</b><span>{t.sub}</span>
            </button>
          ))}
        </div>
        {(opts.inventory?.length ?? 0) > 0 && drafts.length > 0 && (
          <div className="bm-stock" aria-label="Inventory switches">
            <span className="muted small">From the inventory: drag one onto a new switch</span>
            {opts.inventory!.filter((s) => ![...stocked.values()].includes(s.serial)).map((s) => (
              <button key={s.serial} type="button" className="bm-inv" {...draggable({ type: "inv", label: s.serial, serial: s.serial })}>
                <b className="mono">{s.serial}</b><span>{s.model}</span>
              </button>
            ))}
          </div>
        )}
        <p className={`bm-said${said ? " refuse" : ""}`} role="status">
          {said ?? done ?? (pick ? `Now click the other end of the cable from ${pick}, or:` : drafts.length ? "New switches are cabled to every spine or leaf. Drag a switch onto another (or click both) for one more cable." : "Click or drag a tile onto the map.")}
        </p>
        {picked && picked.fresh && (
          <button type="button" className="danger small" onClick={() => { onDrop(picked.name); setPick(null); }}>
            Remove {picked.name} from this batch
          </button>
        )}
        {picked && !picked.fresh && canOperate && !opts.fresh && (
          status(picked.name) === "active" ? (
            <p className="muted small">{picked.name} is active: decommission it on its Settings tab before removing it.</p>
          ) : removal ? (
            <div className="bm-confirm">
              <span className="small">
                Remove <b>{removal.devices.join(" and ")}</b> from {site}?
                {removal.links.length > 0 && <> Its cables to {removal.links.join(", ")} go too: those switches lose the link at their next deploy.</>}
                {removal.unpair && <> {removal.unpair} becomes a single switch again.</>}
              </span>
              <span className="inline">
                <button type="button" className="danger small" disabled={busy} onClick={() => remove(picked.name)}>{busy ? "Removing…" : "Remove"}</button>
                <button type="button" className="small" onClick={() => setRemoval(null)}>Cancel</button>
              </span>
            </div>
          ) : (
            <button type="button" className="small" onClick={() => askRemoval(picked.name)}>Remove {picked.name} from the fabric…</button>
          )
        )}
      </div>
    </div>
  );
}

function natural(a: string, b: string) {
  return a.localeCompare(b, undefined, { numeric: true });
}
