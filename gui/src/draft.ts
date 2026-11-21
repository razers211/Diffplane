// Draft mode: changes prepared for a change window. While a draft is open
// in this tab, every request carries its number: pages show the fabric as the draft would make it
// and every save goes into the draft (the orchestrator keeps the live intent untouched).

export const DRAFT_CHANGED = "fabric:draft";       // draft mode entered or left
export const DRAFT_SAVED = "fabric:draft-saved";   // a change was saved into the draft

export interface Draft {
  site: string;
  id: number;
}

const KEY = "fabric.draft";

export function currentDraft(): Draft | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    return value && typeof value.id === "number" && typeof value.site === "string" ? value : memory;
  } catch {
    return memory;
  }
}

export function setDraft(draft: Draft | null): void {
  memory = draft;
  try {
    if (draft) sessionStorage.setItem(KEY, JSON.stringify(draft));
    else sessionStorage.removeItem(KEY);
  } catch {
    // private window: draft mode lasts until the page is reloaded
  }
  window.dispatchEvent(new Event(DRAFT_CHANGED));
}

let memory: Draft | null = null;

/** The header every request carries in draft mode. */
export function draftHeaders(): Record<string, string> {
  const draft = currentDraft();
  return draft ? { "X-Change-Set": String(draft.id) } : {};
}

export interface ChangeSetStep {
  method: string;
  path: string;
  body: string | null;
  user: string;
  at: string;
}

export interface ChangeSet {
  id: number;
  site: string;
  author: string;
  created_at: string;
  status: "open" | "scheduled" | "applied" | "discarded";
  steps: ChangeSetStep[];
}

const STEP_WORDS: [string, RegExp, string][] = [
  ["POST", /^\/segments$/, "Segment created"],
  ["PATCH", /^\/segments\/[^/]+\/\d+$/, "Segment changed"],
  ["DELETE", /^\/segments\/[^/]+\/\d+$/, "Segment deleted"],
  ["POST", /^\/segments\/[^/]+\/\d+\/attachments$/, "Port attached"],
  ["PATCH", /^\/segments\/[^/]+\/\d+\/attachments$/, "Port VLAN changed"],
  ["DELETE", /^\/segments\/[^/]+\/\d+\/attachments/, "Port detached"],
  ["PUT", /^\/segments\/[^/]+\/\d+\/routing$/, "Segment routing changed"],
  ["PUT", /^\/segments\/[^/]+\/\d+\/relay$/, "DHCP relay changed"],
  ["PUT", /^\/segments\/[^/]+\/\d+\/ipv6$/, "Segment IPv6 changed"],
  ["POST", /^\/services$/, "Service created"],
  ["DELETE", /^\/services\//, "Service deleted"],
  ["", /^\/services\//, "Service changed"],
  ["POST", /^\/devices\/[^/]+\/trunks$/, "Trunk created"],
  ["DELETE", /^\/devices\/[^/]+\/trunks\//, "Trunk deleted"],
  ["", /^\/routing\//, "Routing changed"],
  ["", /^\/fabrics\/[^/]+\/microsegmentation\//, "Security groups changed"],
  ["", /^\/fabrics\/[^/]+\/qos\//, "QoS changed"],
  ["", /^\/fabrics\/[^/]+\/intent-snapshots\//, "Rolled back to a rollback point"],
  ["", /^\/fabrics\/[^/]+\/(ntp|snmp|gateway-mac|netstream|syslog)$/, "Fabric setting changed"],
];

/** A recorded change in plain words, with the name it touches when the body says one. */
export function stepText(step: ChangeSetStep): string {
  const path = step.path.split("?")[0];
  const found = STEP_WORDS.find(([m, re]) => (!m || m === step.method) && re.test(path));
  let body: Record<string, unknown> = {};
  try {
    body = step.body ? JSON.parse(step.body) : {};
  } catch {
    // shown without a name
  }
  const name = ["name", "device", "interface"].map((k) => body[k]).filter((v) => typeof v === "string").join(" ");
  const words = found ? found[2] : `${step.method} ${path}`;
  return name ? `${words}: ${name}` : words;
}
