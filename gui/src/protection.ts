import type { ProtectionMode, ProtectionTemplate, ProtectionTrip, ProtectionView } from "./api";

// Port protection: what a mode and a template mean in plain words, and
// the lines a switch gets (the PortProtection generator's, repeated here for the Expert view).

export const MODES: { mode: ProtectionMode; label: string; text: string }[] = [
  { mode: "off", label: "Off", text: "No protection is written to any switch." },
  { mode: "watch", label: "Watch", text: "Storms and loops are logged and alerted; ports never shut." },
  { mode: "on", label: "On", text: "Storm traffic is dropped; a port that loops or gets a switch's BPDU shuts and reopens after a pause." },
];

export const TEMPLATES: { template: ProtectionTemplate | null; label: string; text: string }[] = [
  { template: "server", label: "Server", text: "Storm limits, loop detection and BPDU guard (shuts if a switch is plugged in)." },
  { template: "hypervisor", label: "Hypervisor", text: "Storm limits and loop detection, no BPDU guard: virtual switches may send BPDUs." },
  { template: null, label: "None", text: "No protection on this port." },
];

const STORM_WORDS: Record<string, string> = { broadcast: "broadcast", multicast: "unknown multicast", "unknown-unicast": "unknown unicast" };
const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });

/** What a protected port does in this mode, one sentence per threat. */
export function effects(mode: ProtectionMode, template: ProtectionTemplate, plan: ProtectionView["plan"]): string[] {
  if (mode === "off") return [];
  const on = mode === "on";
  const shut = `the port shuts and reopens after ${plan.recovery} s`;
  const limits = Object.entries(plan.storm).map(([k, p]) => `${STORM_WORDS[k] ?? k} above ${p} %`).join(", ");
  return [
    `${limits[0].toUpperCase()}${limits.slice(1)} of the port's speed: ${on ? "the excess is dropped, the port stays up" : "logged and alerted"}.`,
    `A loop back into this port: ${on ? shut : "logged and alerted"}.`,
    ...(plan.templates[template]?.edge ? [`A switch plugged in here (a BPDU): ${on ? shut : "nothing until protection is On"}.`] : []),
  ];
}

/** The lines one switch gets: `mine` its protected ports and trunks, `members` each trunk's ports. */
export function switchLines(mode: ProtectionMode, plan: ProtectionView["plan"], mine: Record<string, ProtectionTemplate>,
                            members: Record<string, string[]>): string[] {
  if (mode === "off") return [];
  const on = mode === "on";
  const storm = [
    ...Object.entries(plan.storm).map(([k, p]) => ` storm control ${k} min-rate percent ${p} max-rate percent ${p}`),
    ...(on ? [" storm control action block"] : []),
    " storm control enable log", " storm control enable trap",
  ];
  const ports = Object.keys(mine).sort(byName);
  const out: string[] = [];
  if (on && ports.length) {
    out.push("stp bpdu-protection",
      ...["bpdu-protection", "loopback-detect", "storm-control"].map((c) => `error-down auto-recovery cause ${c} interval ${plan.recovery}`));
  }
  for (const name of ports) {
    const trunk = members[name];
    for (const m of trunk ?? []) out.push(`interface ${m}`, ...storm);
    out.push(`interface ${name}`, ...(trunk ? [] : storm));
    if (plan.templates[mine[name]]?.edge) out.push(" stp edged-port enable");
    out.push(" loopback-detect enable");
    if (on) out.push(" loopback-detect action error-down", ` loopback-detect recovery-time ${plan.recovery}`);
  }
  return out;
}

/** The trips of a port or trunk (a trunk's storm control acts on its members). */
export function tripsOf(tripped: ProtectionTrip[], device: string, port: string, members: string[] = []): ProtectionTrip[] {
  return tripped.filter((t) => t.device === device && (t.port === port || members.includes(t.port)));
}

/** Every protected port and trunk of the fabric, switch by switch. */
export function protectedRows(ports: ProtectionView["ports"]): { device: string; port: string; template: ProtectionTemplate }[] {
  return Object.entries(ports).sort(([a], [b]) => byName(a, b))
    .flatMap(([device, mine]) => Object.keys(mine).sort(byName).map((port) => ({ device, port, template: mine[port] })));
}
