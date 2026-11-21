import type { AuditEvent, DeployJob, DriftDevice } from "./api";
import { windowText } from "./windowPlan";

// What is waiting between intent and the switches: the pending bar, the
// Changes badge and the Changes page read it from the latest diffs (drift or a check) and the
// deploy jobs. A saved change shows up once a diff has run: the GUI starts one after each save.

export const DEPLOYS_CHANGED = "fabric:deploys-changed";   // a deploy approved, cancelled or prepared: the bar reloads now
export const INTENT_SAVED = "fabric:intent-saved";

/** A request that changes intent the switches get (and so may make a diff non-empty). */
export function isIntentWrite(method: string, path: string): boolean {
  if (method.toUpperCase() === "GET") return false;
  const p = path.split("?")[0];
  return /^\/(segments|services)(\/|$)/.test(p)
    || /^\/routing\/[^/]+\/devices\//.test(p)
    || /^\/devices\/[^/]+\/(trunks|management|role)(\/|$)/.test(p)
    || /^\/devices\/[^/]+$/.test(p)
    || /^\/fabrics\/[^/]+\/(ntp|snmp|gateway-mac|switches|spines|leaf-pairs)$/.test(p)
    || /^\/fabrics\/[^/]+\/snmp\/(v2c|copy|readers\/[^/]+|traps\/[^/]+)$/.test(p)   // SNMP (passwords are not intent)
    || /^\/fabrics\/[^/]+\/microsegmentation\//.test(p)
    || /^\/fabrics\/[^/]+\/qos\//.test(p)
    || /^\/devices\/[^/]+\/port-settings\//.test(p) || /^\/fabrics\/[^/]+\/port-settings\/enabled$/.test(p)
    || /^\/devices\/[^/]+\/protection\//.test(p) || /^\/fabrics\/[^/]+\/protection\/mode$/.test(p)
    || /^\/dci\//.test(p)
    || /^\/fabrics\/[^/]+\/intent-snapshots\/\d+\/rollback$/.test(p);
}

export type Step = "saved" | "review" | "approve" | "deploy";
export const STEPS: { step: Step; label: string }[] = [
  { step: "saved", label: "Saved" },
  { step: "review", label: "Review the patch" },
  { step: "approve", label: "Approve" },
  { step: "deploy", label: "Deploy and verify" },
];

const PREPARING = ["queued", "preparing"];
const ROLLING = ["approved", "deploying", "reapproval"];   // reapproval: a window waits for an approver

export interface Pending {
  outOfSync: DriftDevice[];      // latest diff not empty
  failed: DriftDevice[];         // the latest check could not read the switch
  awaiting: DeployJob | null;    // prepared, waiting for an approver
  preparing: DeployJob | null;   // patches being computed (or queued behind a rollout)
  rolling: DeployJob | null;     // approved, being deployed
  scheduled: DeployJob[];        // approved for a change window not started yet, soonest first
  step: Step | null;             // where the fabric is on the way to the switches; null: nothing waits
}

const mine = (site: string, jobSite: string | null) => !site || jobSite === site || jobSite === null;

export function pending(site: string, drift: DriftDevice[], deploys: DeployJob[]): Pending {
  const devices = drift.filter((d) => !site || d.site === site);
  const jobs = deploys.filter((j) => mine(site, j.site));   // newest first, as the API lists them
  const rolling = jobs.find((j) => ROLLING.includes(j.status)) ?? null;
  const awaiting = jobs.find((j) => j.status === "diffed") ?? null;
  const preparing = jobs.find((j) => PREPARING.includes(j.status)) ?? null;
  const outOfSync = devices.filter((d) => d.state === "out_of_sync");
  const failed = devices.filter((d) => d.state === "error");
  const scheduled = jobs.filter((j) => j.status === "scheduled")
    .sort((a, b) => Date.parse(a.window_start ?? "") - Date.parse(b.window_start ?? ""));
  const step: Step | null = rolling ? "deploy" : awaiting ? "approve" : preparing || outOfSync.length ? "review" : null;
  return { outOfSync, failed, awaiting, preparing, rolling, scheduled, step };
}

/** The bar's sentence, or null when nothing waits. */
export function barText(p: Pending): string | null {
  const n = p.outOfSync.length;
  const switches = `${n} switch${n === 1 ? "" : "es"}`;
  if (p.rolling?.status === "reapproval") return `Deploy #${p.rolling.id} waits for an approver: a switch differs at its window's start.`;
  if (p.rolling) return `Deploy #${p.rolling.id} is rolling out.`;
  if (p.awaiting) return `Deploy #${p.awaiting.id} waits for approval.`;
  if (p.preparing) return `Deploy #${p.preparing.id} is being prepared.`;
  const next = p.scheduled[0];
  const later = next?.window_start && next.window_end
    ? `Deploy #${next.id} is scheduled for ${windowText(new Date(next.window_start), new Date(next.window_end))}.` : null;
  if (n) return `${switches} differ${n === 1 ? "s" : ""} from intent: saved changes not deployed yet, or changed by hand.${later ? ` ${later}` : ""}`;
  return later;
}

const INTENT = /^(segment|service|routing|trunk|fabric|device|microseg|qos|dci|ports|protection)\./;
const NOT_INTENT = ["fabric.create", "fabric.import", "fabric.delete", "device.location"];

/** Intent changes since the fabric's last verified deploy, newest first (from the audit trail,
 *  which has no fabric column: an event is the fabric's when its detail names the fabric or one
 *  of its switches). */
export function intentChanges(site: string, audit: AuditEvent[], deploys: DeployJob[], switches: string[]): AuditEvent[] {
  const last = deploys.find((j) => j.status === "verified" && mine(site, j.site));
  const since = last?.finished_at ? Date.parse(last.finished_at) : 0;
  const names = new Set([site, ...switches].filter(Boolean));
  const ours = (e: AuditEvent) => !site || (e.detail ?? "").split(/[\s:=,]+/).some((w) => names.has(w));
  return audit.filter((e) => INTENT.test(e.action) && !NOT_INTENT.includes(e.action) && Date.parse(e.at) > since && ours(e));
}

const WORDS: Record<string, string> = {
  "segment.create": "Segment created", "segment.delete": "Segment deleted", "segment.rename": "Segment renamed",
  "segment.attach": "Port attached", "segment.detach": "Port detached", "segment.change": "Port VLAN changed",
  "segment.routing": "Segment routing changed", "service.create": "Service created", "service.change": "Service changed",
  "service.delete": "Service deleted", "routing.set": "Routing intent changed", "trunk.create": "Trunk created",
  "trunk.delete": "Trunk deleted", "fabric.ntp": "NTP changed", "fabric.snmp": "SNMP location or contact changed",
  "fabric.gateway-mac": "Gateway MAC changed", "fabric.add-switches": "Switches added",
  "fabric.add-spine": "Spine added", "fabric.add-leaf-pair": "Leaf pair added", "fabric.remove-device": "Switch removed",
  "fabric.rollback": "Rolled back to a rollback point", "fabric.rollback-stopped": "Rollback stopped",
  "microseg.mode": "Microsegmentation mode changed", "microseg.group": "Security group changed",
  "microseg.inside": "Security group inside rule changed", "microseg.contract": "Contract changed",
  "qos.enabled": "QoS turned on or off", "qos.mark": "QoS mark changed",
  "ports.enabled": "Port settings managed or not", "ports.set": "Port settings changed",
  "protection.mode": "Port protection Off, Watch or On", "protection.port": "Port protection template changed",
  "fabric.snmp.v2c": "SNMP v2c turned on or off", "fabric.snmp.reader": "SNMP reader added or changed",
  "fabric.snmp.reader.delete": "SNMP reader removed", "fabric.snmp.trap": "SNMP trap target changed",
  "fabric.snmp.trap.delete": "SNMP trap target removed", "fabric.snmp.copy": "SNMP readers copied from another fabric",
  "dci.link": "DC link changed", "dci.segment": "Stretched segment changed",
  "device.activate": "Switch activated", "device.role": "Switch role changed", "device.decommission": "Switch decommissioned",
};
export const describe = (action: string) => WORDS[action] ?? action;
