// The fabric overview: what needs attention, from data the platform already has.
// Pure, so it is unit tested; Overview.tsx only renders it.

import type { DeployJob, Device, DriftDevice, Topology } from "./api";

export interface OverviewData {
  alerts: { alert: string; device: string | null; severity: string | null; since: number | null }[];
  // Control-plane problems: BGP/OSPF peers down, M-LAG heartbeat lost; planned partners aside.
  control?: { device: string; problem: string }[];
  ports: { device: string; interface: string; bps: number; in_bps: number | null; out_bps: number | null; history: number[] }[];
  telemetry: { ok: boolean; error: string | null };
}

export type Tone = "good" | "warn" | "bad" | "info";

export interface Tile {
  key: "switches" | "sync" | "collector" | "alerts" | "approvals";
  label: string;
  value: number;
  of?: number;
  sub: string;
  tone: Tone;
}

export interface Attention {
  tone: Tone;
  title: string;
  detail: string;
  href: string;
  action: string;
}

/** Port speed in bits/s from the Huawei port name (10GE1/0/1 is 10 Gbps); null when unknown (trunks). */
export function portSpeed(name: string): number | null {
  if (name.startsWith("MEth")) return 1e9;
  const match = /^(\d*)GE\d+\/\d+\/\d+$/.exec(name);
  if (!match) return null;
  return (match[1] ? Number(match[1]) : 1) * 1e9;
}

/** Load in percent of the port's speed, the busier direction; null when the speed is unknown. */
export function load(port: { interface: string; in_bps: number | null; out_bps: number | null }): number | null {
  const speed = portSpeed(port.interface);
  if (!speed) return null;
  return Math.min(100, (100 * Math.max(port.in_bps ?? 0, port.out_bps ?? 0)) / speed);
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const list = (names: string[]) => (names.length > 2 ? `${names.slice(0, 2).join(", ")} and ${names.length - 2} more` : names.join(", "));

export function summarize(input: {
  site: string;
  devices: Device[];
  drift: DriftDevice[];
  topology: Topology | null;
  deploys: DeployJob[];
  overview: OverviewData | null;
}): { tiles: Tile[]; attention: Attention[]; history: DeployJob[] } {
  const { site } = input;
  const devices = input.devices.filter((d) => !site || d.site === site);
  const active = devices.filter((d) => d.status === "active");
  const names = new Set(devices.map((d) => d.name));
  const drift = input.drift.filter((d) => names.has(d.device));
  const deploys = input.deploys.filter((j) => !site || j.site === site || j.site === null);
  const inSync = drift.filter((d) => d.state === "in_sync");
  const outOfSync = drift.filter((d) => d.state === "out_of_sync");
  const failed = drift.filter((d) => d.state === "error");
  const collected = new Set((input.topology?.nodes ?? []).filter((n) => n.collected).map((n) => n.name));
  const notPolled = active.filter((d) => !collected.has(d.name));
  const waiting = deploys.filter((j) => j.status === "diffed");
  const alerts = input.overview?.alerts ?? [];

  const tiles: Tile[] = [
    { key: "switches", label: "Switches", value: devices.length, sub: `${active.length} active · ${devices.length - active.length} planned`, tone: "info" },
    {
      key: "sync", label: "In sync", value: inSync.length, of: active.length,
      sub: outOfSync.length ? `${list(outOfSync.map((d) => d.device))} out of sync` : failed.length ? `check failed on ${list(failed.map((d) => d.device))}` : active.length ? "every active switch matches intent" : "no active switch",
      tone: failed.length ? "bad" : outOfSync.length ? "warn" : "good",
    },
    {
      key: "collector", label: "Collector", value: active.length - notPolled.length, of: active.length,
      sub: input.topology === null ? "telemetry unavailable" : notPolled.length ? `not polled: ${list(notPolled.map((d) => d.name))}` : "polling every 5 s",
      tone: input.topology === null || notPolled.length ? "warn" : "good",
    },
    { key: "alerts", label: "Alerts", value: alerts.length, sub: alerts.length ? list([...new Set(alerts.map((a) => a.alert))]) : "none firing", tone: alerts.length ? "bad" : "good" },
    { key: "approvals", label: "Approvals", value: waiting.length, sub: waiting.length ? `deploy ${waiting.map((j) => `#${j.id}`).join(", ")} waiting` : "nothing waiting", tone: waiting.length ? "info" : "good" },
  ];

  const attention: Attention[] = [];
  for (const job of waiting) {
    const changed = job.steps.filter((s) => s.status === "diffed").length;
    attention.push({ tone: "info", title: `Approve deploy #${job.id}`, detail: `${plural(changed, "device")} to change · requested by ${job.requested_by ?? "unknown"}`, href: `#/changes/deploys/${job.id}`, action: "Review" });
  }
  for (const d of failed) attention.push({ tone: "bad", title: `Drift check failed on ${d.device}`, detail: d.error ?? "see the Drift tab", href: "#/changes", action: "Open" });
  for (const d of outOfSync) attention.push({ tone: "bad", title: `${d.device} out of sync`, detail: "the switch differs from intent", href: "#/changes", action: "Diff" });
  for (const d of notPolled) if (input.topology !== null) attention.push({ tone: "warn", title: `${d.name} not polled`, detail: "the collector has no recent poll", href: `#/switches/${encodeURIComponent(d.name)}`, action: "Device" });
  for (const link of input.topology?.links ?? []) {
    if (link.status !== "missing" && link.status !== "mismatch") continue;
    const [a, b] = link.ends;
    attention.push({ tone: "warn", title: `Link ${link.status}: ${a.device} ${a.interface}`, detail: `cabled to ${b.device} ${b.interface} in NetBox`, href: "#/overview", action: "Map" });
  }
  for (const c of input.overview?.control ?? []) {
    attention.push({ tone: "warn", title: `${c.device}: control plane`, detail: c.problem,
      href: `#/switches/${encodeURIComponent(c.device)}/control`, action: "Open" });
  }
  for (const alert of alerts) if (!alert.device) attention.push({ tone: "bad", title: alert.alert, detail: "fabric-wide alert", href: "#/changes", action: "Open" });
  const order: Record<Tone, number> = { info: 0, bad: 1, warn: 2, good: 3 };
  attention.sort((x, y) => order[x.tone] - order[y.tone]);

  return { tiles, attention, history: deploys.slice(0, 5) };
}

/** A deploy's tone on the timeline. */
export function deployTone(status: DeployJob["status"]): Tone {
  if (status === "verified" || status === "unchanged") return "good";
  if (status === "failed") return "bad";
  return "info";
}
