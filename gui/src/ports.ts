// Ports & segments by drag and drop. Pure, so it is unit tested;
// Ports.tsx renders it and the orchestrator checks everything again.

import type { Port, Segment } from "./api";

/** A drop target names its switch and port (or trunk). */
export const targetOf = (device: string, iface: string) => `${device}|${iface}`;
export const splitTarget = (target: string) => {
  const at = target.indexOf("|");
  return { device: target.slice(0, at), iface: target.slice(at + 1) };
};

/** The VLAN the attach popover proposes: the bridge-domain ID when it is a valid VLAN. */
export const presetVid = (bdId: number) => (bdId >= 1 && bdId <= 4094 ? String(bdId) : "");

/** Why this port cannot take the segment untagged, or null. One untagged segment per port. */
export function untaggedBlocker(port: Port | undefined, segments: Segment[]): string | null {
  const vni = port?.segments.find((s) => s.vid === null)?.vni;
  if (vni === undefined) return null;
  return `already carries ${segments.find((s) => s.vni === vni)?.name ?? `VNI ${vni}`} untagged`;
}

/** Whether the segment is already on this port. */
export const alreadyOn = (port: Port | undefined, vni: number) => !!port?.segments.some((s) => s.vni === vni);

/** A segment's colour slot (1-6), by its place in the fabric's list sorted by VNI. */
export function colourOf(segments: Segment[], vni: number): number {
  const i = [...segments].sort((a, b) => a.vni - b.vni).findIndex((s) => s.vni === vni);
  return i < 0 ? 0 : (i % 6) + 1;   // shortcut: colours repeat after six segments; the chip's name tells them apart
}
