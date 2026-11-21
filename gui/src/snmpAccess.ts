import type { SnmpAccess } from "./api";

// Monitoring access: what the Snmp generator writes for the page's Expert view, and the
// plain-language checks the forms show before the API would refuse.

export const GROUP = "FABRIC-RO";
export const NAME = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;

/** The generator's lines (generators/snmp.py), the community masked; location and contact left out. */
export function switchLines(a: SnmpAccess): string[] {
  const readers = Object.keys(a.readers).sort();
  const lines = a.v2c && readers.length ? ["snmp-agent sys-info version v2c v3"]
    : a.v2c ? ["snmp-agent sys-info version v2c", "snmp-agent sys-info version v3 disable"]
    : ["snmp-agent sys-info version v3"];
  if (a.v2c) lines.push("snmp-agent community read ****** alias ro-…");
  if (readers.length) lines.push(`snmp-agent group v3 ${GROUP} privacy notify-view ViewDefault`);
  for (const r of readers) lines.push(`snmp-agent usm-user v3 ${r}`, `snmp-agent usm-user v3 ${r} group ${GROUP}`);
  for (const [name, t] of Object.entries(a.traps).sort(([x], [y]) => x.localeCompare(y))) {
    if (a.readers[t.reader]) {
      lines.push(`snmp-agent target-host host-name ${name} trap address udp-domain ${t.address} vpn-instance _management_vpn_ params securityname ${t.reader} v3 privacy`);
    }
  }
  if (readers.length) lines.push("snmp-agent protocol source-interface MEth0/0/0", "snmp-agent protocol vpn-instance _management_vpn_");
  return lines;
}

/** A reader's state on one switch, in plain words (orchestrator/snmp_passwords.py reader_state). */
export const READER_STATE: Record<string, string> = {
  ok: "ready",
  missing: "not deployed yet",
  "no-passwords": "passwords not typed yet",
  different: "other algorithms: type the passwords again",
};

/** Why v2c cannot be turned off now, or null. */
export const v2cOffBlocked = (a: SnmpAccess) => (Object.keys(a.readers).length ? null : "Add a v3 reader first, or nothing could read the switches.");

/** Why a reader cannot be removed now, or null. */
export function removeBlocked(a: SnmpAccess, reader: string): string | null {
  const traps = Object.entries(a.traps).filter(([, t]) => t.reader === reader).map(([n]) => n).sort();
  if (traps.length) return `Trap targets ${traps.join(", ")} send as ${reader}: remove or move them first.`;
  if (!a.v2c && Object.keys(a.readers).length === 1) return "The last reader while v2c is off: turn v2c on first.";
  return null;
}

/** The switch's complexity rule as the API checks it (orchestrator/snmp_passwords.py check). */
export function passwordProblem(value: string): string | null {
  if (value.length < 8 || value.length > 64 || /[\s?"']/.test(value)) return "8-64 characters, no spaces, '?' or quotes.";
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((k) => k.test(value)).length;
  return kinds < 3 ? "Use three of lower case, upper case, digits and symbols." : null;
}
