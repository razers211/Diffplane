// Lay out the orchestrator's topology for the isometric map (plain SVG).
// Pure, so it is unit tested.
//
// Each fabric is a band: spines on top, leaves below, grouped into M-LAG pairs found from the
// cables between two leaves (the peer-link); each pair stands on its own floor. A pair of spines
// cabled to each other is the gateway pair of a centralized fabric. Devices not in NetBox (seen
// only by LLDP) go on a row of their own. Parallel cables between two devices are one bundle.
import type { FabricTraffic, LinkStatus, Topology, TopologyLink, TopologyNode } from "./api";
import { portSpeed } from "./overview";

export const PROBLEM_STATUSES: LinkStatus[] = ["missing", "mismatch", "unexpected"];

export function linkId(link: TopologyLink): string {
  const [a, b] = link.ends;
  return `${link.source}:${a.device}:${a.interface}--${b.device}:${b.interface}`;
}

export function problemCount(topology: Topology): number {
  return topology.links.filter((l) => PROBLEM_STATUSES.includes(l.status)).length;
}

export type Kind = "spine" | "leaf" | "other";

export interface PlacedNode {
  name: string;
  x: number;
  y: number;
  kind: Kind;
  node: TopologyNode;
}

export interface Floor {
  cx: number;
  cy: number;
  w: number;
  h: number;
  label: string;
  gateway: boolean;
}

export interface Bundle {
  id: string;
  a: string;
  b: string;
  links: TopologyLink[];
  status: LinkStatus;
  peer: boolean; // between the two devices of a pair
}

export interface Band {
  site: string | null;
  y: number;
  height: number;
}

export interface Layout {
  width: number;
  height: number;
  nodes: PlacedNode[];
  floors: Floor[];
  bundles: Bundle[];
  bands: Band[];
}

const LEAF_ROLES = ["leaf", "border-leaf"];
const kindOf = (node: TopologyNode): Kind =>
  !node.in_netbox ? "other" : node.role === "spine" ? "spine" : LEAF_ROLES.includes(node.role ?? "") ? "leaf" : "other";

/** Natural order: east-leaf2 before east-leaf10. */
export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true });
}

// Worst first: a bundle shows its worst cable.
const SEVERITY: LinkStatus[] = ["missing", "mismatch", "unexpected", "unverified", "ok"];
const worst = (links: TopologyLink[]): LinkStatus =>
  SEVERITY.find((s) => links.some((l) => l.status === s)) ?? "ok";

export const bundleKey = (a: string, b: string) => [a, b].sort(naturalCompare).join(" -- ");

/** Groups of devices joined by a cable to each other (pairs), each sorted, in natural order. */
export function pairs(names: string[], links: TopologyLink[]): string[][] {
  const set = new Set(names);
  const parent = new Map(names.map((n) => [n, n]));
  const find = (n: string): string => (parent.get(n) === n ? n : find(parent.get(n)!));
  for (const link of links) {
    const [a, b] = link.ends.map((e) => e.device);
    if (a !== b && set.has(a) && set.has(b)) parent.set(find(a), find(b));
  }
  const groups = new Map<string, string[]>();
  for (const n of names) groups.set(find(n), [...(groups.get(find(n)) ?? []), n]);
  return [...groups.values()].map((g) => g.sort(naturalCompare)).sort((x, y) => naturalCompare(x[0], y[0]));
}

// Horizontal room for a pair of leaves, and the gap between its two boxes: wide enough that
// two names of about 11 characters (east-leaf10) never touch (44 was too tight).
const SLOT = 150;
/** The name under a box: a name too long for PAIR_GAP loses its fabric's prefix (the band names the fabric),
 *  unless another switch on the map is already called that. */
export const mapLabel = (node: { name: string; site?: string | null }, names: ReadonlySet<string> = new Set()) => {
  const short = node.site && node.name.startsWith(`${node.site}-`) ? node.name.slice(node.site.length + 1) : node.name;
  return node.name.length > 11 && !names.has(short) ? short : node.name;
};
const PAIR_GAP = 70;
const BAND = 330; // height of one fabric's band
const OTHER_ROW = 80;

export function layout(topology: Topology, gatewayPair: string[] | null = null): Layout {
  const sites = [...new Set(topology.nodes.filter((n) => n.in_netbox).map((n) => n.site))].sort((a, b) =>
    naturalCompare(a ?? "", b ?? ""),
  );
  const nodes: PlacedNode[] = [];
  const floors: Floor[] = [];
  const bands: Band[] = [];
  let top = 0;
  let width = 400;

  for (const site of sites) {
    const inSite = topology.nodes.filter((n) => n.in_netbox && n.site === site);
    const spines = inSite.filter((n) => kindOf(n) === "spine").map((n) => n.name).sort(naturalCompare);
    const leaves = inSite.filter((n) => kindOf(n) !== "spine").map((n) => n.name).sort(naturalCompare);
    const leafGroups = pairs(leaves, topology.links);
    const spineGroups = pairs(spines, topology.links);
    const byName = new Map(inSite.map((n) => [n.name, n]));

    const leafRow = leafGroups.reduce((w, g) => w + (g.length > 1 ? SLOT : SLOT * 0.6) + 8, 40);
    const spineRow = spines.length * 150 + 120;
    const bandWidth = Math.max(leafRow, spineRow, 400);
    width = Math.max(width, bandWidth);
    const spineY = top + 72;
    const leafY = top + 252;

    // Spines: centred, a pair kept close together on its floor.
    let x = bandWidth / 2 - ((spines.length - 1) * 150) / 2;
    for (const group of spineGroups) {
      const start = x;
      for (const name of group) {
        nodes.push({ name, x, y: spineY, kind: "spine", node: byName.get(name)! });
        x += 150;
      }
      if (group.length > 1) {
        const cx = (start + x - 150) / 2;
        const gateway = !!gatewayPair && group.every((n) => gatewayPair.includes(n));
        // Name above the box (from about -29) to the box's bottom (+13), with 10 to spare all round.
        floors.push({ cx, cy: spineY - 8, w: 150 * (group.length - 1) + 150, h: 62, gateway, label: gateway ? "Gateway pair · M-LAG" : "Spine pair · M-LAG" });
      }
    }

    // Leaves: pairs side by side, each on a floor.
    x = (bandWidth - leafRow) / 2 + 40;
    leafGroups.forEach((group, i) => {
      const slot = group.length > 1 ? SLOT : SLOT * 0.6;
      const step = group.length > 1 ? PAIR_GAP / (group.length - 1) : 0;
      const first = x + slot / 2 - (group.length > 1 ? PAIR_GAP / 2 : 0);
      group.forEach((name, k) => {
        nodes.push({ name, x: first + k * step, y: leafY, kind: kindOf(byName.get(name)!), node: byName.get(name)! });
      });
      if (group.length > 1) {
        const gateway = !!gatewayPair && group.every((n) => gatewayPair.includes(n));
        // Box (11 above and below its centre) and name under it (to about +27), with 9 to spare all round.
        floors.push({ cx: x + slot / 2, cy: leafY + 8, w: slot - 6, h: 56, gateway, label: gateway ? "Gateway pair" : `pair ${i + 1}` });
      }
      x += slot + 8;
    });

    bands.push({ site, y: top, height: BAND });
    top += BAND;
  }

  // Devices not in NetBox (LLDP neighbours): one row at the bottom.
  const others = topology.nodes.filter((n) => !n.in_netbox).map((n) => n.name).sort(naturalCompare);
  if (others.length) {
    const byName = new Map(topology.nodes.map((n) => [n.name, n]));
    others.forEach((name, i) => {
      nodes.push({ name, x: 60 + i * 120, y: top + 40, kind: "other", node: byName.get(name)! });
    });
    width = Math.max(width, 60 + others.length * 120);
    top += OTHER_ROW;
  }

  // Bundles: every cable between the same two devices is drawn once.
  const grouped = new Map<string, TopologyLink[]>();
  for (const link of topology.links) {
    const [a, b] = link.ends.map((e) => e.device);
    const key = bundleKey(a, b);
    grouped.set(key, [...(grouped.get(key) ?? []), link]);
  }
  const kinds = new Map(nodes.map((n) => [n.name, n.kind]));
  const bundles: Bundle[] = [...grouped.entries()].map(([id, links]) => {
    const [a, b] = id.split(" -- ");
    return { id, a, b, links, status: worst(links), peer: kinds.get(a) === kinds.get(b) && kinds.get(a) !== "other" };
  });

  return { width, height: Math.max(top, 200), nodes, floors, bundles, bands };
}

// -- traffic view -----------------------------------------------------------------


/** A line's load: the busiest direction of the busiest port at either end of any of its
 * cables, as a share of that port's speed (null when no end has data or a known speed). */
export function bundleLoad(bundle: Bundle, traffic: FabricTraffic): { load: number | null; bps: number | null } {
  let load: number | null = null;
  let bps: number | null = null;
  for (const link of bundle.links) {
    for (const end of link.ends) {
      const t = traffic[end.device]?.[end.interface];
      if (!t) continue;
      const rate = Math.max(t.in_bps ?? 0, t.out_bps ?? 0);
      bps = Math.max(bps ?? 0, rate);
      const speed = portSpeed(end.interface);
      if (speed) load = Math.max(load ?? 0, Math.min(1, rate / speed));
    }
  }
  return { load, bps };
}

/** How long a traffic dot takes to run along a link: busier links run faster. */
export const flowSeconds = (load: number) => Number((4.5 - 3.5 * Math.min(1, Math.max(0, load))).toFixed(2));

