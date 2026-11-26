// The guided "New segment" flow: checks for each step and the writes it makes.
// Pure, so it is unit tested; NewSegment.tsx renders and runs it. The orchestrator checks every
// write again (uniqueness, ranges, ports); these checks only catch mistakes while typing.

import type { Segment } from "./api";
import { parseVid } from "./vid";

export const MAX_ID = 16_777_215;            // 24-bit VNI and bridge-domain range
export const MAX_L2_VNI = MAX_ID - 1_000_000; // L3 VNI = VNI + 1,000,000 (symmetric fabrics)

export interface PortChoice {
  device: string;
  port: string;
  trunk: boolean; // an M-LAG trunk: written on both leaves of the pair
  vid: string;    // "" = untagged
}

export interface Draft {
  name: string;
  vni: string;
  bd: string;
  ports: PortChoice[];
  routed: boolean;
  prefixLength: string;
  subnet: string;   // the operator's own gateway network ("" = the next free one of the pool)
  firewall: string; // "" = no default route
  peers: number[];
  relay: string;    // DHCP servers in the segment's own VPN instance, comma or space separated; "" = no relay
  ipv6: "" | "slaac" | "dhcpv6" | "none";   // "" = IPv4 only; else dual-stack and how hosts get addresses
}

export const emptyDraft = (): Draft => ({
  name: "", vni: "", bd: "", ports: [], routed: false, prefixLength: "24", subnet: "", firewall: "", peers: [], relay: "", ipv6: "",
});

/** The DHCP relay servers typed in the wizard. */
export const relayServers = (d: Draft): string[] => d.relay.split(/[\s,]+/).filter(Boolean);

/** The VNI suggested for a bridge domain (the convention: BD 500 has VNI 10500). */
export const suggestedVni = (bd: number) => 10_000 + bd;

const whole = (text: string) => /^\d+$/.test(text.trim());

/** VRP's VPN instance name limit (orchestrator: generators/fabric.py VPN_NAME_MAX). */
export const VPN_NAME_MAX = 31;

/** A routed segment's VPN instance on the switches: <name>-<bridge domain>. */
export const segmentVpn = (name: string, bd: number | string) => `${name}-${bd}`;

/** Why this name cannot be a segment's (it names the VPN instance <name>-<bd>), or null. */
export function nameError(name: string, bd: number | string): string | null {
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(name)) return "Start with a letter; only letters, digits, - and _";
  const vpn = segmentVpn(name, bd);
  if (vpn.length > VPN_NAME_MAX) return `Too long: its VPN instance ${vpn} has ${vpn.length} characters, at most ${VPN_NAME_MAX}`;
  return null;
}

/** Problems with the segment step (name, VNI, BD), by field. */
export function segmentErrors(d: Draft, existing: Segment[]): Partial<Record<"name" | "vni" | "bd", string>> {
  const errors: Partial<Record<"name" | "vni" | "bd", string>> = {};
  const name = d.name.trim();
  if (!name) errors.name = "Give the segment a name";
  else if (nameError(name, whole(d.bd) ? d.bd : "1")) errors.name = nameError(name, whole(d.bd) ? d.bd : "1")!;
  else if (existing.some((s) => s.name === name)) errors.name = `${name} already exists in this fabric`;
  if (!whole(d.bd) || Number(d.bd) < 1 || Number(d.bd) > MAX_ID) errors.bd = `A number from 1 to ${MAX_ID}`;
  else if (existing.some((s) => s.bd_id === Number(d.bd))) errors.bd = `Bridge domain ${d.bd} is taken`;
  if (!whole(d.vni) || Number(d.vni) < 1 || Number(d.vni) > MAX_L2_VNI) errors.vni = `A number from 1 to ${MAX_L2_VNI}`;
  else if (existing.some((s) => s.vni === Number(d.vni))) errors.vni = `VNI ${d.vni} is taken`;
  return errors;
}

/** Problems with the ports step, one per port (null when fine). */
export function portErrors(d: Draft): (string | null)[] {
  return d.ports.map((p, i) => {
    const vid = parseVid(p.vid);
    if (!vid.ok) return vid.error;
    const same = d.ports.findIndex((q) => q.device === p.device && q.port === p.port);
    if (same !== i) return `${p.device} ${p.port} is listed twice`;
    return null;
  });
}

/** Why a typed gateway network is not usable, or null: an IPv4 network /8-/30 on its boundary. */
export function subnetError(text: string): string | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec(text.trim());
  if (!m || m.slice(1, 5).some((o) => Number(o) > 255)) return "Type the network as an IPv4 address and length, e.g. 10.20.30.0/24";
  const len = Number(m[5]);
  if (len < 8 || len > 30) return "The gateway network is /8 to /30";
  const ip = m.slice(1, 5).reduce((a, o) => a * 256 + Number(o), 0);
  if (ip % 2 ** (32 - len) !== 0) return `${text.trim()} is not on a /${len} boundary`;
  return null;
}

/** Problems with the routing step. */
export function routingErrors(d: Draft): string | null {
  if (!d.routed) return null;
  if (d.subnet.trim()) return subnetError(d.subnet);
  if (!whole(d.prefixLength) || Number(d.prefixLength) < 8 || Number(d.prefixLength) > 30) return "The gateway prefix length is 8 to 30";
  const servers = relayServers(d);
  const ipv4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
  const bad = servers.find((a) => !ipv4.test(a));
  if (bad) return `DHCP server ${bad} is not an IPv4 address`;
  if (servers.length > 8) return "At most 8 DHCP servers";
  return null;
}

export type Write =
  | { kind: "create"; label: string }
  | { kind: "attach"; label: string; port: PortChoice }
  | { kind: "route"; label: string }
  | { kind: "ipv6"; label: string }
  | { kind: "relay"; label: string };

const vidText = (vid: string) => (vid.trim() ? `VLAN ${vid.trim()}` : "untagged");

/** The writes Create makes, in order: the segment, each port, then routing. */
export function plan(d: Draft): Write[] {
  const writes: Write[] = [{ kind: "create", label: `Create ${d.name.trim()} (VNI ${d.vni}, bridge domain ${d.bd})` }];
  for (const port of d.ports) {
    writes.push({
      kind: "attach", port,
      label: `Attach to ${port.device} ${port.port}${port.trunk ? " (both leaves of the pair)" : ""}, ${vidText(port.vid)}`,
    });
  }
  if (d.routed) {
    const route = d.firewall ? `default route via ${d.firewall}` : "no default route";
    const peers = d.peers.length ? `, peered with ${d.peers.length} segment${d.peers.length > 1 ? "s" : ""}` : "";
    writes.push({ kind: "route", label: `Route it: ${d.subnet.trim() ? `gateway network ${d.subnet.trim()}` : `a /${d.prefixLength} gateway`}, ${route}${peers}` });
    if (d.ipv6) writes.push({ kind: "ipv6", label: `IPv6 too: a /64, hosts on ${d.ipv6 === "slaac" ? "SLAAC" : d.ipv6 === "dhcpv6" ? "DHCPv6" : "manual addresses"}` });
    const servers = relayServers(d);
    if (servers.length) writes.push({ kind: "relay", label: `Relay DHCP to ${servers.join(", ")}` });
  }
  return writes;
}

/** Where the gateway of a routed segment lives, in words. */
export function gatewayPlace(architecture: "symmetric" | "centralized" | undefined, gatewayPair: string[] | null, d: Draft): string {
  if (architecture === "centralized") return `on the gateway pair ${(gatewayPair ?? []).join(" + ")}`;
  const leaves = [...new Set(d.ports.map((p) => p.device))];
  return leaves.length ? `on every leaf it is attached to (${leaves.join(", ")}${d.ports.some((p) => p.trunk) ? " and their pair" : ""})` : "on every leaf it is attached to (none yet)";
}

/** The switches the routing diagram shows the gateway on: the gateway pair in a centralized
 *  fabric, otherwise the leaves the segment is attached to. */
export function gatewaySwitches(architecture: "symmetric" | "centralized" | undefined, gatewayPair: string[] | null, d: Draft): string[] {
  if (architecture === "centralized") return gatewayPair ?? [];
  return [...new Set(d.ports.map((p) => p.device))];
}

/** The VNI shown for a bridge domain while the VNI follows it: 10000 + BD, empty while the
 *  BD is not a number. Once the VNI is typed in by hand it no longer follows. */
export const followingVni = (bd: string) => (/^\d+$/.test(bd.trim()) ? String(suggestedVni(Number(bd))) : "");

/** Gateway subnet sizes offered as chips; any other length 8-30 can be typed. */
export const PREFIX_CHOICES = ["24", "25", "26", "27", "28", "29", "30"];
