// Routing intent as the orchestrator stores it (orchestrator/routing_intent.py), and pure
// edits on it, so they are unit tested. The orchestrator validates every save; these only
// build the document.

export type Action = "permit" | "deny";

export interface PrefixRule {
  index: number;
  action: Action;
  prefix: string;
  ge?: number | null;
  le?: number | null;
}
export interface AsPathRule {
  index: number;
  action: Action;
  regex: string;
}
export interface CommunityRule {
  index: number;
  action: Action;
  community: string;
}
export interface PolicyNode {
  node: number;
  action: Action;
  match_ip_prefix?: string | null;
  apply_local_preference?: number | null;
}
export interface StaticRoute {
  vpn?: string | null; // absent: the global table
  prefix: string;
  next_hop: string; // an address or NULL0
  preference?: number | null;
}
export interface BgpPeer {
  vpn?: string | null;
  address: string;
  asn: number;
  import_policy?: string | null;
  export_policy?: string | null;
  import_prefix_list?: string | null;
  export_prefix_list?: string | null;
}
export interface Aggregate {
  vpn?: string | null;
  prefix: string;
  detail_suppressed?: boolean;
  suppress_policy?: string | null;
}
export interface OspfProcess {
  process: number;
  vpn: string;
  router_id: string;
  areas?: string[];
}
export interface DeviceRouting {
  prefix_lists?: Record<string, PrefixRule[]>;
  as_path_filters?: Record<string, AsPathRule[]>;
  community_filters?: Record<string, CommunityRule[]>;
  route_policies?: Record<string, PolicyNode[]>;
  static_routes?: StaticRoute[];
  bgp_peers?: BgpPeer[];
  aggregates?: Aggregate[];
  ospf?: OspfProcess[];
}

export interface RoutingDocument {
  devices?: Record<string, DeviceRouting>;
  retired?: Record<string, DeviceRouting>;
  retired_services?: Record<string, string[]>;
  retired_done?: Record<string, string[]>;
}

export interface RoutingTarget {
  target: string; // a device, or an M-LAG pair "<leaf>+<leaf>"
  devices: { name: string; role: string | null; status: string | null }[];
  vpns: string[]; // VPN instances its routing objects may use, besides the global table
  pair?: string | null; // a switch of an M-LAG pair on its own: its pair's target
}

export interface ImportReport {
  device: string;
  imported: string[];
  unmanaged: string[];
  error: string | null;
  at: number | null; // unix seconds
}

export type NamedKind = "prefix_lists" | "as_path_filters" | "community_filters" | "route_policies";
export type KeyedKind = "static_routes" | "bgp_peers" | "aggregates" | "ospf";
type Rule = PrefixRule | AsPathRule | CommunityRule | PolicyNode;
type Item = StaticRoute | BgpPeer | Aggregate | OspfProcess;

export const NAMED: NamedKind[] = ["prefix_lists", "as_path_filters", "community_filters", "route_policies"];
export const KEYED: KeyedKind[] = ["static_routes", "bgp_peers", "aggregates", "ospf"];

const number = (rule: Rule) => ("node" in rule ? rule.node : rule.index);

/** The identity the orchestrator uses (routing_intent.py `key`): the same object twice is refused. */
export function keyOf(kind: KeyedKind, item: Item): string {
  switch (kind) {
    case "static_routes": {
      const s = item as StaticRoute;
      return [s.vpn ?? "", s.prefix, s.next_hop].join(" ");
    }
    case "bgp_peers": {
      const p = item as BgpPeer;
      return [p.vpn ?? "", p.address].join(" ");
    }
    case "aggregates": {
      const a = item as Aggregate;
      return [a.vpn ?? "", a.prefix].join(" ");
    }
    case "ospf":
      return String((item as OspfProcess).process);
  }
}

/** Add a rule to a named list (created if new), replacing the rule with the same index/node. */
export function putRule(routing: DeviceRouting, kind: NamedKind, name: string, rule: Rule): DeviceRouting {
  const lists = { ...(routing[kind] as Record<string, Rule[]> | undefined) };
  const rules = (lists[name] ?? []).filter((r) => number(r) !== number(rule));
  lists[name] = [...rules, rule].sort((a, b) => number(a) - number(b));
  return { ...routing, [kind]: lists };
}

/** Remove one rule; a list left empty is removed. */
export function removeRule(routing: DeviceRouting, kind: NamedKind, name: string, num: number): DeviceRouting {
  const lists = { ...(routing[kind] as Record<string, Rule[]> | undefined) };
  const rules = (lists[name] ?? []).filter((r) => number(r) !== num);
  if (rules.length) lists[name] = rules;
  else delete lists[name];
  return { ...routing, [kind]: lists };
}

/** Add an object, replacing the one with the same identity (an edit). */
export function putItem(routing: DeviceRouting, kind: KeyedKind, item: Item): DeviceRouting {
  const items = ((routing[kind] ?? []) as Item[]).filter((i) => keyOf(kind, i) !== keyOf(kind, item));
  return { ...routing, [kind]: [...items, item] };
}

export function removeItem(routing: DeviceRouting, kind: KeyedKind, key: string): DeviceRouting {
  return { ...routing, [kind]: ((routing[kind] ?? []) as Item[]).filter((i) => keyOf(kind, i) !== key) };
}

/** Number of objects (a named list counts once, as on the switch). */
export function count(routing: DeviceRouting | undefined): number {
  if (!routing) return 0;
  return (
    NAMED.reduce((n, kind) => n + Object.keys(routing[kind] ?? {}).length, 0) +
    KEYED.reduce((n, kind) => n + (routing[kind] ?? []).length, 0)
  );
}

/** Names defined in a named kind, for the reference pickers. */
export const names = (routing: DeviceRouting, kind: NamedKind) => Object.keys(routing[kind] ?? {}).sort();

// -- form fields --------------------------------------------------------------------------

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

/** An integer field; empty gives null when optional. */
export function parseNumber(text: string, label: string, min: number, max: number, optional = false): Parsed<number | null> {
  const trimmed = text.trim();
  if (trimmed === "") return optional ? { ok: true, value: null } : { ok: false, error: `${label} is required` };
  if (!/^\d+$/.test(trimmed)) return { ok: false, error: `${label} must be a whole number` };
  const value = Number(trimmed);
  if (value < min || value > max) return { ok: false, error: `${label} must be ${min}-${max}` };
  return { ok: true, value };
}

/** OSPF areas typed as "0.0.0.0, 0.0.0.1" (or numbers, written as VRP shows them: dotted). */
export function parseAreas(text: string): Parsed<string[]> {
  const parts = text.split(/[\s,]+/).filter(Boolean);
  const areas: string[] = [];
  for (const part of parts) {
    if (/^\d+$/.test(part)) {
      const n = Number(part);
      if (n > 4294967295) return { ok: false, error: `area ${part} is too large` };
      areas.push([n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join("."));
    } else if (/^\d{1,3}(\.\d{1,3}){3}$/.test(part) && part.split(".").every((o) => Number(o) <= 255)) {
      areas.push(part);
    } else {
      return { ok: false, error: `${part} is not an area (a number or a dotted address)` };
    }
  }
  return { ok: true, value: areas };
}

/** "leaf1+leaf2" -> "leaf1 + leaf2". */
export const targetLabel = (target: string) => target.split("+").join(" + ");

const KIND_LABELS: Record<NamedKind | KeyedKind, [string, string]> = {
  prefix_lists: ["prefix list", "prefix lists"],
  as_path_filters: ["AS-path filter", "AS-path filters"],
  community_filters: ["community filter", "community filters"],
  route_policies: ["route-policy", "route-policies"],
  static_routes: ["static route", "static routes"],
  bgp_peers: ["BGP peer", "BGP peers"],
  aggregates: ["aggregate", "aggregates"],
  ospf: ["OSPF process", "OSPF processes"],
};

/** "2 prefix lists", "1 BGP peer"…: what a target holds, kinds with none left out. */
export function kindsSummary(routing: DeviceRouting | undefined): string[] {
  if (!routing) return [];
  return [...NAMED, ...KEYED].flatMap((kind) => {
    const n = NAMED.includes(kind as NamedKind) ? Object.keys(routing[kind as NamedKind] ?? {}).length : (routing[kind as KeyedKind] ?? []).length;
    return n ? [`${n} ${KIND_LABELS[kind][n === 1 ? 0 : 1]}`] : [];
  });
}

/** An import error's first useful line (Annet prints a traceback; its last line says why). */
export function shortError(error: string): string {
  const lines = error.split("\n").map((l) => l.trim()).filter(Boolean);
  const last = [...lines].reverse().find((l) => /error|exception|failed|refused|timeout|denied|mismatch/i.test(l) && !l.startsWith("File "));
  return (last ?? lines[0] ?? error).slice(0, 200);
}
