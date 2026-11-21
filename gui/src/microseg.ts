// Microsegmentation: Huawei EPGs and contracts. Pure helpers for the
// Security groups page: services as people type them, and the contract matrix.

export type MicrosegMode = "off" | "learn" | "enforce";
export type Protocol = "any" | "tcp" | "udp" | "icmp" | number;

export interface GbpService {
  protocol: Protocol;
  ports?: [number, number];
}

export interface Epg {
  id: number;
  segment: number;
  group: string | null;     // null: the segment's own EPG
  name: string | null;      // null while its segment is not routed
  active: boolean;
  inside: "permit" | "deny";
  prefixes: string[];
  macs: string[];
}

export interface Contract {
  from: number;
  to: number;
  services: GbpService[];
  precedence: number;
}

export interface Usage {
  epgs: number;
  members: number;
  rules: number;
  limits: { epgs: number; members: number; rules: number };
}

export interface Microseg {
  mode: MicrosegMode;
  epgs: Epg[];
  contracts: Contract[];
  usage: Usage;
}

export const MODES: { mode: MicrosegMode; label: string; text: string }[] = [
  { mode: "off", label: "Off", text: "Nothing on the switches." },
  { mode: "learn", label: "Learn", text: "Groups and contracts on the switches, everything still allowed: counters show what flows." },
  { mode: "enforce", label: "Enforce", text: "Only what a contract allows passes between groups." },
];

/** One service as shown: "TCP 5432", "UDP 53-54", "ICMP", "any", "protocol 47". */
export function serviceText(s: GbpService): string {
  if (s.protocol === "any") return "any";
  const name = typeof s.protocol === "number" ? `protocol ${s.protocol}` : s.protocol.toUpperCase();
  if (!s.ports) return name;
  const [a, b] = s.ports;
  return `${name} ${a === b ? a : `${a}-${b}`}`;
}

export const servicesText = (list: GbpService[]) => list.map(serviceText).join(", ");

/** Services typed as "tcp 5432, udp 53-54, icmp, any, 47": a list, or the reason it is wrong. */
export function parseServices(text: string): GbpService[] | string {
  const items = text.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!items.length) return "type at least one service, e.g. tcp 443 or any";
  const out: GbpService[] = [];
  for (const item of items) {
    const [first, ports, ...rest] = item.split(/\s+/);
    if (rest.length) return `"${item}": a protocol, then one port or a range`;
    let protocol: Protocol;
    if (first === "any" || first === "tcp" || first === "udp" || first === "icmp") protocol = first;
    else if (/^\d+$/.test(first) && Number(first) <= 255) protocol = Number(first);
    else return `"${first}": any, tcp, udp, icmp or a protocol number 0-255`;
    const service: GbpService = { protocol };
    if (ports !== undefined) {
      if (protocol !== "tcp" && protocol !== "udp") return `"${item}": ports are only for tcp or udp`;
      const m = /^(\d+)(?:-(\d+))?$/.exec(ports);
      if (!m) return `"${ports}": one port or a range like 8000-8080`;
      const a = Number(m[1]), b = Number(m[2] ?? m[1]);
      if (a > b || b > 65535) return `"${ports}": ports 0-65535, the lower first`;
      service.ports = [a, b];
    }
    out.push(service);
  }
  return out;
}

/** The text a service list is edited as (the reverse of parseServices). */
export const servicesInput = (list: GbpService[]) => list.map((s) => serviceText(s).replace("protocol ", "").toLowerCase()).join(", ");

/** Prefixes or MACs typed with commas, spaces or new lines. */
export const parseList = (text: string) => text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);

/** The active EPGs in matrix order: each segment's own EPG, then its groups. */
export function matrixEpgs(epgs: Epg[]): Epg[] {
  const active = epgs.filter((e) => e.active);
  const segments = active.filter((e) => e.group === null).sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
  return segments.flatMap((s) => [s, ...active.filter((e) => e.group !== null && e.segment === s.segment)
    .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""))]);
}

export const contractOf = (contracts: Contract[], from: number, to: number) =>
  contracts.find((c) => c.from === from && c.to === to);

/** What a matrix cell means in this mode: from -> to without a contract. */
export function noContract(mode: MicrosegMode, from: Epg, to: Epg): string {
  if (from.id === to.id) return from.inside === "deny" ? "denied inside" : "allowed inside";
  if (mode === "enforce") return "denied";
  return mode === "learn" ? "allowed (learn)" : "not filtered";
}

/** A resource's share of the switch's limit, as a percentage (rounded up, so 1 is never 0). */
export const share = (used: number, limit: number) => (limit ? Math.ceil((used / limit) * 100) : 0);

export interface Chip {
  slot: string; chip: string;
  tcam_block_total: number; tcam_block_used: number; tcam_block_limit: number;
  epg_used: number; epg_free: number; epg_member_used: number; epg_member_free: number; gbp_rule_used: number; gbp_rule_free: number;
}
export interface Counters {
  matched_packets: number; matched_bytes: number; passed_packets: number; passed_bytes: number; dropped_packets: number; dropped_bytes: number;
}
export interface SwitchState {
  device: string;
  chips: Chip[];
  contracts: Record<string, Counters>;          // by classifier GBP-<from>-<to>
  failed_rules: { classifier: string; rule: number; state: string }[];
  failed_members: { epg: number; member: string; vpn: string; state: string }[];
}

/** The classifier a contract is on the switch (generators/microsegmentation.py). */
export const classifierOf = (c: { from: number; to: number }) => `GBP-${c.from}-${c.to}`;

/** A contract's counters as one short line: "12 matched · 3 dropped". */
export function countersText(c: Counters | undefined): string {
  if (!c) return "not on this switch";
  return `${c.matched_packets.toLocaleString("en")} matched · ${c.dropped_packets.toLocaleString("en")} dropped`;
}

// -- the group map: groups on a ring, an arrow per contract --------------

/** Where each of `n` groups sits on a ring, the first at the top, clockwise. */
export function ringPoints(n: number, cx: number, cy: number, r: number): { x: number; y: number }[] {
  return Array.from({ length: n }, (_, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(n, 1);
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
}

/** An arrow between two group circles of radius `gap`, bent sideways so A→B and B→A never overlap. */
export function arrowPath(a: { x: number; y: number }, b: { x: number; y: number }, gap: number): string {
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const [sx, sy, ex, ey] = [a.x + ux * gap, a.y + uy * gap, b.x - ux * gap, b.y - uy * gap];
  const bend = Math.min(40, len / 5);
  const [mx, my] = [(sx + ex) / 2 - uy * bend, (sy + ey) / 2 + ux * bend];
  return `M${sx.toFixed(1)} ${sy.toFixed(1)} Q${mx.toFixed(1)} ${my.toFixed(1)} ${ex.toFixed(1)} ${ey.toFixed(1)}`;
}

/** The services box with one more quick pick: "any" replaces the rest, nothing is added twice. */
export function addService(text: string, pick: string): string {
  const list = text.split(",").map((s) => s.trim()).filter(Boolean);
  if (pick === "any" || list.includes("any")) return pick;
  return list.includes(pick) ? list.join(", ") : [...list, pick].join(", ");
}
