// Format traffic for people. Pure, so it is unit tested.

// Units: Mbps, Gbps or Tbps, never bps or kbps.
export function formatBps(bps: number | null | undefined): string {
  if (bps === null || bps === undefined || !Number.isFinite(bps)) return "—";
  if (bps >= 1e12) return `${(bps / 1e12).toFixed(2)} Tbps`;
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`;
  return `${(bps / 1e6).toFixed(2)} Mbps`;
}

// Errors and discards over the last 5 minutes: Prometheus' increase() extrapolates, so round.
export function formatCount(count: number | null | undefined): string {
  if (count === null || count === undefined || !Number.isFinite(count)) return "—";
  return String(Math.round(count));
}

// Chart axes: a round scale and short labels in the same units.

/** A round axis maximum and its step: 1, 2 or 5 times a power of ten, four steps. */
export function niceScale(max: number): { top: number; step: number } {
  if (!(max > 0)) return { top: 1e6, step: 2.5e5 };
  const raw = max / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? 10 * power);
  return { top: step * Math.ceil(max / step), step };
}

/** Axis labels in the GUI's units (Mbps, Gbps or Tbps), as short as the scale allows. */
export function axisBps(v: number, top: number): string {
  const [div, unit] = top >= 1e12 ? [1e12, "Tbps"] : top >= 1e9 ? [1e9, "Gbps"] : [1e6, "Mbps"];
  const n = v / div;
  return `${Number.isInteger(n) ? n : n < 10 ? n.toFixed(1) : Math.round(n)} ${unit}`;
}
