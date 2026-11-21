import type { Rack } from "./api";

// Where a switch is: labels and free U positions. Pure, so it is unit tested; the
// orchestrator checks positions again.

export function placeLabel(p: { location: string | null; rack: string | null; position: number | null; face: string | null }): string {
  if (!p.rack) return "not placed";
  return `${p.location ?? "?"} / ${p.rack}${p.position ? ` · U${p.position}${p.face ? ` ${p.face}` : ""}` : ""}`;
}

/** Lowest U positions where a device `height` U high fits in the rack, ignoring `self`. */
export function freePositions(rack: Rack, height: number, self: string): number[] {
  const taken = new Set<number>();
  for (const d of rack.devices) {
    if (d.name === self || d.position === null) continue;
    for (let u = d.position; u < d.position + d.u_height; u++) taken.add(u);
  }
  const free: number[] = [];
  for (let u = 1; u + height - 1 <= rack.u_height; u++) {
    let fits = true;
    for (let k = u; k < u + height; k++) if (taken.has(k)) fits = false;
    if (fits) free.push(u);
  }
  return free.reverse(); // top of the rack first, as racks are read
}
