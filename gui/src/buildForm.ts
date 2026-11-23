import type { BuildOptions, ImportSession } from "./api";

// The add-switches form (several switches at once): leaf pairs, lone leaves,
// spines and, in a centralized fabric, M-LAG spine pairs (one of them may be an existing single
// spine; the pair may become the gateway pair); links between new and existing switches; an
// optional management address per new switch.
// Pure, so it is unit tested; the orchestrator checks everything again before writing anything.

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/;
const ADDRESS = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;

export type Kind = "pair" | "leaf" | "spine" | "spinepair";
export interface SwitchDraft {
  kind: Kind;
  names: string[]; // two for a pair
  model: string;
  role: "leaf" | "border-leaf" | "spine";
  peerLinkTrunk: string;
  peerLinkMembers: string[];
  management: string[]; // per name, "" = added planned
  inventory: string[]; // per name, serial of the inventory switch it is, "" = none
  gateway: boolean; // spine pair, or border-leaf pair (centralized): becomes the fabric's gateway pair
}
export interface LinkDraft {
  spine: string;
  spine_port: string;
  leaf: string;
  leaf_port: string;
}

const PAIRS: Kind[] = ["pair", "spinepair"];
const SPINES: Kind[] = ["spine", "spinepair"];
/** The model of a switch placed before it is known: it has no ports, so no cables, until it is
 *  detected and placed (Detect switches, Place as); it then takes the model and ports it reports. */
export const GENERIC = "";

export function emptySwitch(kind: Kind, model: string): SwitchDraft {
  const two = PAIRS.includes(kind);
  return {
    kind, names: two ? ["", ""] : [""], model, role: SPINES.includes(kind) ? "spine" : "leaf",
    peerLinkTrunk: "0", peerLinkMembers: [], management: two ? ["", ""] : [""], inventory: two ? ["", ""] : [""],
    gateway: false,
  };
}

const existing = (opts: BuildOptions, name: string) => opts.devices.find((d) => d.name === name);
const clean = (d: SwitchDraft) => d.names.map((n) => n.trim()).filter(Boolean);
/** Names of the switches a draft creates (an existing spine joining a spine pair is not one). */
const created = (opts: BuildOptions, d: SwitchDraft) => clean(d).filter((n) => !existing(opts, n));
const newOf = (drafts: SwitchDraft[], name: string) => drafts.find((d) => d.names.some((n) => n.trim() === name));

export const spineChoices = (opts: BuildOptions, drafts: SwitchDraft[]) => [
  ...opts.devices.filter((d) => d.role === "spine").map((d) => d.name),
  ...drafts.filter((d) => SPINES.includes(d.kind)).flatMap((d) => created(opts, d)),
];
export const leafChoices = (opts: BuildOptions, drafts: SwitchDraft[]) => [
  ...opts.devices.filter((d) => d.role !== "spine").map((d) => d.name),
  ...drafts.filter((d) => !SPINES.includes(d.kind)).flatMap((d) => created(opts, d)),
];
/** Existing single spines a spine pair may take in. */
export const unpairedSpines = (opts: BuildOptions) =>
  opts.devices.filter((d) => d.role === "spine" && !d.paired).map((d) => d.name);

/** Ports a pair's peer-link may use: its model's, and free on an existing member. */
export function peerLinkChoices(opts: BuildOptions, d: SwitchDraft): string[] {
  let ports = opts.models[d.model] ?? [];
  for (const n of clean(d)) {
    const found = existing(opts, n);
    if (found) ports = ports.filter((p) => found.free_ports.includes(p));
  }
  return ports;
}

/** Ports a link row may use on a device: a new switch's model ports, or an existing switch's
 * free ports; never a pair's peer-link members, nor those other rows already use. */
export function portChoices(opts: BuildOptions, drafts: SwitchDraft[], links: LinkDraft[], device: string, row: number): string[] {
  const used = links.flatMap((l, i) => (i === row ? [] : [l.spine === device ? l.spine_port : "", l.leaf === device ? l.leaf_port : ""]))
    .filter(Boolean);
  const draft = newOf(drafts, device);
  const found = existing(opts, device);
  const ports = found ? found.free_ports : draft ? opts.models[draft.model] ?? [] : [];
  const members = draft ? draft.peerLinkMembers : [];
  return ports.filter((p) => !used.includes(p) && !members.includes(p));
}

const speed = (port: string) => Number(/^(\d*)GE/.exec(port)?.[1] || "1");

/** A new cable between a spine and a leaf: the fastest free port at each end, or null
 *  when an end has none left. */
export function cableOn(opts: BuildOptions, drafts: SwitchDraft[], links: LinkDraft[], spine: string, leaf: string): LinkDraft | null {
  const best = (device: string) => {
    const ports = portChoices(opts, drafts, links, device, links.length);
    return ports.find((p) => speed(p) === Math.max(...ports.map(speed))) ?? "";
  };
  const [spine_port, leaf_port] = [best(spine), best(leaf)];
  return spine_port && leaf_port ? { spine, spine_port, leaf, leaf_port } : null;
}

/** Cables for switches just placed: a new leaf to every spine, a new spine to every leaf,
 *  existing or new; none where an end has no free port left. */
export function autoLinks(opts: BuildOptions, drafts: SwitchDraft[], links: LinkDraft[], names: string[], spine: boolean): LinkDraft[] {
  const out = [...links];
  for (const n of names) {
    for (const other of spine ? leafChoices(opts, drafts) : spineChoices(opts, drafts)) {
      if (names.includes(other)) continue;
      const [s, l] = spine ? [n, other] : [other, n];
      if (out.some((x) => x.spine === s && x.leaf === l)) continue;
      const link = cableOn(opts, drafts, out, s, l);
      if (link) out.push(link);
    }
  }
  return out;
}

function addressProblem(name: string, address: string): string | null {
  const m = ADDRESS.exec(address.trim());
  if (!m || m.slice(1, 5).some((o) => Number(o) > 255) || Number(m[5]) > 30) {
    return `${name}: management address with its mask, e.g. 172.24.0.120/24`;
  }
  return null;
}

/** Why the form cannot be sent yet, or null. */
export function batchProblem(opts: BuildOptions, drafts: SwitchDraft[], links: LinkDraft[]): string | null {
  if (drafts.length === 0) return "Add at least one switch";
  const names = drafts.flatMap((d) => created(opts, d));
  const seen = new Set<string>();
  const spare = unpairedSpines(opts);
  for (const d of drafts) {
    for (const n of d.names.map((x) => x.trim())) {
      if (!NAME.test(n)) return `${n ? `"${n}"` : "A name"}: 1–63 letters, digits, ".", "_" or "-"`;
      if (seen.has(n)) return `${n} appears twice`;
      seen.add(n);
      if (existing(opts, n) && !(d.kind === "spinepair" && spare.includes(n))) {
        return d.kind === "spinepair" ? `${n} is not a single spine of this fabric` : `${n} already exists in this fabric`;
      }
    }
    if (d.model === GENERIC) return `${clean(d).join(" and ") || "A generic switch"}: detect it (Detect switches, then Place as) so its model and ports are read`;
    if (!opts.models[d.model]) return `Pick a model for ${d.names[0] || "each switch"}`;
    if (d.kind === "spinepair") {
      if (!opts.centralized) return "Spine pairs are for a centralized fabric";
      if (created(opts, d).length === 0) return `${d.names.join(" and ")}: a spine pair needs at least one new spine`;
    }
    if (PAIRS.includes(d.kind)) {
      if (!opts.bypass) return "This fabric has no M-LAG bypass VLAN, which an M-LAG pair needs";
      const t = d.peerLinkTrunk.trim();
      if (!/^\d+$/.test(t) || Number(t) > 1023) return "Peer-link trunk number: 0–1023";
      if (d.peerLinkMembers.length === 0) return `Pick the peer-link member ports of ${d.names.join(" + ")}`;
      const allowed = peerLinkChoices(opts, d);
      const bad = d.peerLinkMembers.find((p) => !allowed.includes(p));
      if (bad) return `${bad} is not free on ${d.names.join(" and ")}`;
    }
    for (const [i, address] of d.management.entries()) {
      if (address.trim() && !existing(opts, d.names[i].trim())) {
        const p = addressProblem(d.names[i], address);
        if (p) return p;
      }
    }
  }
  const gateways = drafts.filter((d) => canBeGateway(opts, d) && d.gateway);
  if (gateways.length > 1) return "Only one pair can become the gateway pair";
  if (opts.fresh && opts.centralized && gateways.length === 0) {
    return "A centralized fabric needs its gateway pair: mark a spine pair or a border-leaf pair";
  }
  const picked = drafts.flatMap((d) => d.inventory.filter(Boolean));
  if (new Set(picked).size !== picked.length) return "An inventory switch is picked twice";
  for (const d of drafts) {
    for (const [i, serial] of d.inventory.entries()) {
      const stock = serial && opts.inventory?.find((s) => s.serial === serial);
      if (stock && stock.model !== d.model) return `${d.names[i] || "A switch"}: inventory switch ${serial} is a ${stock.model}`;
    }
  }
  const addresses = drafts.flatMap((d) => d.management.filter((_, i) => !existing(opts, d.names[i].trim()))
    .map((a) => a.trim().split("/")[0]).filter(Boolean));
  if (new Set(addresses).size !== addresses.length) return "A management address is given twice";
  const ends = new Set<string>();
  for (const l of links) {
    if (!l.spine || !l.spine_port || !l.leaf || !l.leaf_port) return "Complete every link row";
    if (!names.includes(l.spine) && !names.includes(l.leaf)) return `${l.spine} - ${l.leaf}: a link needs a new switch`;
    for (const end of [`${l.spine} ${l.spine_port}`, `${l.leaf} ${l.leaf_port}`]) {
      if (ends.has(end)) return `${end} is used twice`;
      ends.add(end);
    }
  }
  if (spineChoices(opts, drafts).length > 0) {
    for (const d of drafts.filter((x) => !SPINES.includes(x.kind))) {
      for (const n of d.names.map((x) => x.trim())) {
        if (!links.some((l) => l.leaf === n)) return `${n} needs at least one uplink to a spine`;
      }
    }
  }
  return null;
}

/** A pair that may become the gateway pair: a spine pair, or a border-leaf pair of a centralized fabric. */
export const canBeGateway = (opts: BuildOptions, d: SwitchDraft) =>
  d.kind === "spinepair" || (d.kind === "pair" && !!opts.centralized && d.role === "border-leaf");

/** Inventory switches of a model not picked by another row. */
export const inventoryChoices = (opts: BuildOptions, drafts: SwitchDraft[], d: SwitchDraft, index: number) => {
  const taken = drafts.flatMap((x) => x.inventory.filter((s, i) => s && !(x === d && i === index)));
  return (opts.inventory ?? []).filter((s) => s.model === d.model && !taken.includes(s.serial));
};

/** The request body (POST /fabrics/{site}/switches). */
export function toBatch(drafts: SwitchDraft[], links: LinkDraft[], opts?: BuildOptions) {
  const mgmt = (d: SwitchDraft, i: number) => d.management[i]?.trim() || null;
  const isNew = (n: string) => !opts || !existing(opts, n.trim());
  const pairMgmt = (d: SwitchDraft) =>
    Object.fromEntries(d.names.map((n, i) => [n.trim(), isNew(n) ? mgmt(d, i) : null]).filter(([, a]) => a));
  const trunk = (d: SwitchDraft) => ({ peer_link_trunk: Number(d.peerLinkTrunk), peer_link_members: d.peerLinkMembers });
  const stock = (d: SwitchDraft, i: number) => d.inventory[i] || null;
  const pairStock = (d: SwitchDraft) =>
    Object.fromEntries(d.names.map((n, i) => [n.trim(), isNew(n) ? stock(d, i) : null]).filter(([, s]) => s));
  return {
    spines: drafts.filter((d) => d.kind === "spine").map((d) => ({
      name: d.names[0].trim(), model: d.model, management: mgmt(d, 0), inventory: stock(d, 0),
    })),
    pairs: drafts.filter((d) => d.kind === "pair").map((d) => ({
      leaves: d.names.map((n) => n.trim()), model: d.model, role: d.role, ...trunk(d), management: pairMgmt(d),
      inventory: pairStock(d), gateway: !!opts && canBeGateway(opts, d) && d.gateway,
    })),
    leaves: drafts.filter((d) => d.kind === "leaf").map((d) => ({
      name: d.names[0].trim(), model: d.model, role: d.role, management: mgmt(d, 0), inventory: stock(d, 0),
    })),
    spine_pairs: drafts.filter((d) => d.kind === "spinepair").map((d) => ({
      spines: d.names.map((n) => n.trim()), model: d.model, ...trunk(d), management: pairMgmt(d), inventory: pairStock(d),
      gateway: d.gateway,
    })),
    links,
  };
}

/** The next free names for a new switch: `<prefix>-leafN` or `<prefix>-spineN`, skipping
 *  every name that exists or is already in the batch. */
export function nextNames(opts: BuildOptions, drafts: SwitchDraft[], prefix: string, kind: Kind): string[] {
  const taken = new Set([...opts.devices.map((d) => d.name), ...drafts.flatMap((d) => d.names.map((n) => n.trim()))]);
  const base = `${prefix}-${SPINES.includes(kind) ? "spine" : "leaf"}`;
  const names: string[] = [];
  for (let n = 1; names.length < (PAIRS.includes(kind) ? 2 : 1); n++) {
    if (!taken.has(`${base}${n}`)) names.push(`${base}${n}`);
  }
  return names;
}

/** The form's options with the models of detected switches, each with the ports it reported. */
export function withDetected(opts: BuildOptions, session: ImportSession | null): BuildOptions {
  const found = session?.switches.flatMap((s) => (s.detected ? [s.detected] : [])) ?? [];
  if (!found.length) return opts;
  return { ...opts, models: { ...opts.models, ...Object.fromEntries(found.map((d) => [d.model, Object.keys(d.ports)])) } };
}

/** Cables for a generic switch once detected (`after` = the drafts with its model): as if it had
 *  been placed with that model. A switch that had a model keeps the cables it has. */
export function linksForDetected(opts: BuildOptions, before: SwitchDraft[], after: SwitchDraft[], links: LinkDraft[], name: string): LinkDraft[] {
  const d = newOf(before, name);
  return d && d.model === GENERIC ? autoLinks(opts, after, links, created(opts, d), SPINES.includes(d.kind)) : links;
}

/** A detected switch placed as a new one: its address and model (a pair's model is both leaves'). */
export function placeDetected(drafts: SwitchDraft[], name: string, address: string, model: string): SwitchDraft[] {
  return drafts.map((d) => {
    const j = d.names.findIndex((n) => n.trim() === name);
    if (j < 0) return d.management.includes(address) ? { ...d, management: d.management.map((a) => (a === address ? "" : a)) } : d;
    const changed = d.model !== model;
    return {
      ...d, model, management: d.management.map((a, k) => (k === j ? address : a === address ? "" : a)),
      peerLinkMembers: changed ? [] : d.peerLinkMembers, inventory: d.inventory.map((s, k) => (k === j || changed ? "" : s)),
    };
  });
}

/** The names whose labels drop one line on a map so that no two in a row overlap (x: a label's centre,
 *  label: the text drawn when not the name, charWidth: one character of the map's monospace labels). */
export function staggered(labels: { name: string; label?: string; x: number; row: number }[], charWidth = 6.4): Set<string> {
  const low = new Set<string>();
  const ends = new Map<number, [number, number]>();   // per row: where the last label on each line ends
  for (const l of [...labels].sort((a, b) => a.x - b.x)) {
    const half = ((l.label ?? l.name).length * charWidth) / 2 + 3;
    const end = ends.get(l.row) ?? [-Infinity, -Infinity];
    const line = l.x - half >= end[0] ? 0 : 1;
    if (line) low.add(l.name);
    end[line] = l.x + half;
    ends.set(l.row, end);
  }
  return low;
}
