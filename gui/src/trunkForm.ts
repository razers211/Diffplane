import type { DeviceTrunks } from "./api";

// The new-trunk form: which ports it offers and why it cannot be sent yet. Pure, so it
// is unit tested; the orchestrator checks everything again.

export const MAX_TRUNK = 1023;

/** Ports a new trunk may use: free on this device, or on both devices for an M-LAG trunk. */
export function candidatePorts(data: DeviceTrunks, mlag: boolean): string[] {
  return mlag ? data.pair_free_ports ?? [] : data.free_ports;
}

/** Why the form cannot be sent, or null. */
export function trunkProblem(data: DeviceTrunks, number: string, members: string[], mlag: boolean): string | null {
  const n = Number(number);
  if (!/^\d+$/.test(number.trim()) || n < 1 || n > MAX_TRUNK) return `Trunk number: 1–${MAX_TRUNK}`;
  if (data.trunks.some((t) => t.number === n)) return `Eth-Trunk${n} already exists on ${data.device}`;
  if (mlag && !data.peer) return `${data.device} is not in an M-LAG pair`;
  if (members.length === 0) return "Pick at least one member port";
  const offered = new Set(candidatePorts(data, mlag));
  const taken = members.filter((m) => !offered.has(m));
  if (taken.length) return `Not free${mlag ? " on both devices" : ""}: ${taken.join(", ")}`;
  return null;
}

/** What deleting a trunk would do, or why it is refused. */
export function deleteBlocker(t: { kind: string; segments: number; service: boolean }): string | null {
  if (t.kind === "peer-link") return "the peer-link comes from the fabric YAML";
  if (t.service) return "a service uses it: delete the service first";
  if (t.segments) return `it carries ${t.segments} segment${t.segments > 1 ? "s" : ""}: detach them first`;
  return null;
}
