// DC links: EVPN DCI between two fabrics' border pairs. Pure helpers for
// Fabric › DC links.

export interface DciUplink { port: string; address: string; neighbour: string; asn: number }
export interface DciSide {
  fabric: string;
  pair: string[];
  uplinks: Record<string, DciUplink[]>;
  asn?: number; loopbacks?: Record<string, string>; vtep?: string;   // derived by the orchestrator
}
export interface DciSegment { name: string; vnis: Record<string, number>; dci_vni: number }
export interface DciLink { name: string; split_group: string; sides: DciSide[]; segments: DciSegment[] }
export interface DciDocument { links: DciLink[]; retired: Record<string, { peers?: string[]; ports?: string[] }>; ready: string[] }

/** Active border switches not yet verified with the link: stretching waits for them. */
export function waiting(link: DciLink, ready: string[], active: Set<string>): string[] {
  return link.sides.flatMap((s) => s.pair).filter((s) => active.has(s) && !ready.includes(s)).sort();
}

/** Segments the two fabrics both have by name (a stretched segment keeps its name), with each VNI. */
export function commonSegments(a: { name: string; vni: number }[], b: { name: string; vni: number }[]) {
  const other = new Map(b.map((s) => [s.name, s.vni]));
  return a.filter((s) => other.has(s.name)).map((s) => ({ name: s.name, a: s.vni, b: other.get(s.name)! }))
    .sort((x, y) => x.name.localeCompare(y.name));
}

/** The form's uplink row for one switch, or none when its port is left empty. */
export function uplinkOf(row: { port: string; address: string; neighbour: string; asn: string }): DciUplink[] | string {
  if (!row.port.trim()) return [];
  if (!row.address.trim() || !row.neighbour.trim()) return `${row.port}: give its address and the neighbour`;
  const asn = Number(row.asn);
  if (!Number.isInteger(asn) || asn < 1 || asn > 4294967295) return `${row.port}: the neighbour's ASN is 1-4294967295`;
  return [{ port: row.port.trim(), address: row.address.trim(), neighbour: row.neighbour.trim(), asn }];
}
