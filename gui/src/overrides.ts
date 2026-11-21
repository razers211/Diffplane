import type { OverrideEntry } from "./api";

// Found on import: the areas in the order the panel shows them, each entry named for a person.

export const AREAS = ["Underlay", "Segments", "Services", "Switch extras"];

export function grouped(entries: OverrideEntry[]): [string, OverrideEntry[]][] {
  return AREAS.map((area): [string, OverrideEntry[]] => [area, entries.filter((e) => e.area === area)])
    .filter(([, list]) => list.length > 0);
}

export const whereItIs = (e: OverrideEntry) =>
  e.target === null ? "The whole fabric" : e.scope === "segment" ? `Segment VNI ${e.target}` : e.target;

export const changedSwitches = (patches: Record<string, string>) =>
  Object.entries(patches).filter(([, patch]) => patch.trim() !== "");
