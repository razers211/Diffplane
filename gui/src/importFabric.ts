import type { ImportChoices, ImportSwitch, Pools } from "./api";

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
