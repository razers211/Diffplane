import type { ImportChoices, ImportPlan, ImportSwitch, OverrideEntry, Pools } from "./api";

// Import an existing fabric: the operator's input, pure so it is tested.

/** Management addresses typed one per line, or separated by commas or spaces. */
export const parseAddresses = (text: string) => [...new Set(text.split(/[\s,;]+/).map((a) => a.trim()).filter(Boolean))];

/** Every key a switch offers (ports 22 and 830): an approval names exactly these. */
export const offeredKeys = (s: ImportSwitch) => [...new Set(Object.values(s.offered).flat().map((k) => k.key))];

export interface ImportDraft {
  name: string;
  pools: Partial<Pools>;             // only the pools the operator changed
  gatewayMac: string;
  roles: Record<string, string>;     // only the roles the operator changed
  architecture: "" | "symmetric" | "centralized";   // "" as found
  gatewayPair: string;               // "a+b"
}

export const emptyDraft = (): ImportDraft => ({ name: "", pools: {}, gatewayMac: "", roles: {}, architecture: "", gatewayPair: "" });

export function toChoices(d: ImportDraft): ImportChoices {
  return {
    name: d.name.trim(),
    pools: Object.fromEntries(Object.entries(d.pools).filter(([, v]) => v && v.trim()).map(([k, v]) => [k, v!.trim()])),
    gateway_mac: d.gatewayMac.trim() || null,
    roles: d.roles,
    architecture: d.architecture || null,
    gateway_pair: d.architecture === "centralized" && d.gatewayPair ? d.gatewayPair.split("+") : null,
  };
}

/** Switches whose patch is not empty: the platform would change them. */
export const changed = (patches: Record<string, string>) => Object.keys(patches).filter((n) => patches[n].trim()).sort();

export interface SwitchReport { name: string; patch: string; kept: OverrideEntry[]; misfits: string[] }

/** The import report: per switch read, its patch, what it keeps as found and what the platform will
 *  not manage; the rest (fabric, segments, services, names no switch has) under the fabric.
 *  Switches with something to look at come first. */
export function importReport(plan: ImportPlan, entries: OverrideEntry[], patches: Record<string, string>) {
  const read = plan.devices.filter((d) => !d.planned).map((d) => d.name);
  const switches: SwitchReport[] = read.map((name) => ({
    name, patch: patches[name] ?? "",
    kept: entries.filter((e) => e.scope === "switch" && e.target === name),
    misfits: plan.misfits[name] ?? [],
  }));
  const quiet = (r: SwitchReport) => !r.patch.trim() && !r.misfits.length;
  switches.sort((a, b) => Number(quiet(a)) - Number(quiet(b)) || a.name.localeCompare(b.name));
  return {
    switches,
    kept: entries.filter((e) => !(e.scope === "switch" && read.includes(e.target ?? ""))),
    misfits: Object.entries(plan.misfits).filter(([where, list]) => !read.includes(where) && list.length > 0),
  };
}
