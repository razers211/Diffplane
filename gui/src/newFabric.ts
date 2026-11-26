import type { NewFabricSettings, Pools } from "./api";

// The new-fabric wizard's settings. Pure, so it is unit tested; the
// orchestrator checks everything again with the seed's own rules before writing anything.

export interface SettingsDraft {
  name: string;
  asn: string;
  architecture: "symmetric" | "centralized";
  vendor: "huawei" | "arista"; // a fabric is one vendor; the models offered follow it
  supernet: string;
  prefixes: Pools | null; // null until an address plan is proposed
  bypassVlan: string;
  gatewayMac: string;
  ntpServers: string;
  ntpVpn: string;
  snmpLocation: string;
  snmpContact: string;
}

export const emptySettings = (): SettingsDraft => ({
  name: "", asn: "", architecture: "symmetric", vendor: "huawei", supernet: "10.0.0.0/16", prefixes: null, bypassVlan: "4093",
  gatewayMac: "", ntpServers: "", ntpVpn: "", snmpLocation: "", snmpContact: "",
});

export const POOL_LABELS: [keyof Pools, string][] = [
  ["loopback", "Loopbacks (LoopBack0, /32 each)"],
  ["vtep", "VTEPs (/32 per pair on LoopBack2, a lone leaf's on LoopBack1)"],
  ["p2p", "Point-to-point (/30 per spine-leaf link)"],
  ["mlag_bypass", "M-LAG bypass (/30 per pair)"],
  ["pip", "PIPs (LoopBack1, /32 per M-LAG switch)"],
  ["gateways", "Segment gateways (/24 per routed segment)"],
];

/** The fabric's site slug, as the seed derives it from the name. */
export const slugOf = (name: string) => name.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");

const NET = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
const isNet = (text: string) => {
  const m = NET.exec(text.trim());
  return !!m && m.slice(1, 5).every((o) => Number(o) <= 255) && Number(m[5]) <= 32;
};

/** Why the settings cannot be sent yet, or null. */
export function settingsProblem(s: SettingsDraft, existing: string[]): string | null {
  if (!/^[A-Za-z0-9][A-Za-z0-9 _-]{0,49}$/.test(s.name.trim())) return "Fabric name: 1–50 letters, digits, spaces, '_' or '-'";
  const slug = slugOf(s.name);
  if (slug === "inventory") return `"${s.name}" is reserved`;
  if (existing.includes(slug)) return `A fabric ${slug} already exists`;
  if (!/^\d+$/.test(s.asn.trim()) || Number(s.asn) < 1 || Number(s.asn) > 4294967295) return "ASN: 1–4294967295";
  if (s.architecture === "symmetric" && Number(s.asn) > 65535) {
    return "A symmetric fabric needs a 2-byte ASN (route targets <ASN>:<L3 VNI>)";
  }
  if (!s.prefixes) return "Propose the address plan";
  for (const [key, label] of POOL_LABELS) {
    if (!isNet(s.prefixes[key])) return `${label.split(" (")[0]}: a subnet such as 10.0.0.0/24`;
  }
  if (!/^\d+$/.test(s.bypassVlan) || Number(s.bypassVlan) < 2 || Number(s.bypassVlan) > 4094) return "Bypass VLAN: 2–4094";
  if (!/^[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}$/.test(s.gatewayMac.trim())) return "Gateway MAC like 0200-00fa-0001";
  return null;
}

/** The request's settings (POST /fabrics). */
export function toSettings(s: SettingsDraft): NewFabricSettings {
  const ntp = s.ntpServers.split(/[\s,]+/).filter(Boolean);
  return {
    name: s.name.trim(), asn: Number(s.asn), architecture: s.architecture, vendor: s.vendor, prefixes: s.prefixes!,
    bypass_vlan: Number(s.bypassVlan), gateway_mac: s.gatewayMac.trim(), ntp_servers: ntp,
    ntp_vpn_instance: ntp.length && s.ntpVpn.trim() ? s.ntpVpn.trim() : null,
    snmp_location: s.snmpLocation.trim() || null, snmp_contact: s.snmpContact.trim() || null,
  };
}
