// The drawn front panel: where each port sits on the drawing. The platform
// knows a model's port names (seed/ports.py), not where they physically are, so the order is
// schematic: ports grouped by speed, two rows, odd numbers on top, a gap every six columns.

export type PortKind = "free" | "segment" | "service" | "trunk" | "peer" | "fabric";
export interface FacePort {
  name: string;
  kind: PortKind;
  detail: string | null;
  trunk: string | null;
  segments: { vni: number; vid: number | null }[];
  off?: boolean;          // turned off (port settings)
  description?: string;   // its name
}
export interface Placed { port: FacePort; x: number; y: number; w: number; h: number; top: boolean; label: string }

const speed = (name: string) => Number(/^(\d*)GE/.exec(name)?.[1] || "1");
const last = (name: string) => Number(name.split("/").pop());

export function layout(ports: FacePort[]): { placed: Placed[]; width: number; height: number } {
  const groups = new Map<number, FacePort[]>();
  for (const p of ports) groups.set(speed(p.name), [...(groups.get(speed(p.name)) ?? []), p]);
  const placed: Placed[] = [];
  let x = 4;
  for (const [gbps, group] of [...groups].sort((a, b) => a[0] - b[0])) {
    const wide = gbps >= 40;
    const [w, h, step] = wide ? [40, 34, 46] : [24, 30, 28];
    const sorted = [...group].sort((a, b) => last(a.name) - last(b.name));
    sorted.forEach((port, i) => {
      const column = Math.floor(i / 2);
      const top = i % 2 === 0;
      placed.push({
        port, w, h, top, label: String(last(port.name)),
        x: x + column * step + Math.floor(column / 6) * (wide ? 0 : 8),
        y: top ? (wide ? 20 : 22) : (wide ? 62 : 64),
      });
    });
    const columns = Math.ceil(sorted.length / 2);
    x += columns * step + Math.floor((columns - 1) / 6) * (wide ? 0 : 8) + 20;
  }
  return { placed, width: Math.max(x, 200), height: 112 };
}

export const KIND_LABEL: Record<PortKind, string> = {
  free: "free", segment: "carries segments", service: "a service port", trunk: "in an Eth-Trunk",
  peer: "M-LAG peer-link", fabric: "link to another switch",
};

/** What a click picks for jobs that take trunks (segments, services): a trunk member picks its trunk. */
export const trunkOrPort = (port: FacePort) => (port.kind === "trunk" && port.trunk ? port.trunk : port.name);

/** Why this port cannot be picked when only `allowed` can (port or trunk names), in plain words. */
export function refusal(port: FacePort, allowed: Set<string>, pick: (p: FacePort) => string = trunkOrPort, free = "not available for this"): string | null {
  if (allowed.has(pick(port))) return null;
  switch (port.kind) {
    case "fabric": return `a link to another switch (${port.detail ?? "fabric"}): it carries nothing else`;
    case "peer": return "part of the M-LAG peer-link between the two leaves";
    case "service": return `already used by ${port.detail ?? "a service"}`;
    case "trunk": return `a member of ${port.detail ?? port.trunk}`;
    case "segment": return "already carries segments";
    default: return free;
  }
}
