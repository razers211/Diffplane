import { describe, expect, it } from "vitest";
import type { Segment, Service } from "./api";
import { ipv6Places } from "./Ipv6";

const seg = (name: string, vni: number, firewall: string | null, peers: number[], ipv6: boolean): Segment => ({
  fabric: "example", name, vni, bd_id: vni - 10000, attachments: [],
  routing: { gateway: "10.100.0.1/24", peers, firewall, ipv6: ipv6 ? { gateway: "fd00:1::1/64", hosts: "slaac", servers: [] } : null },
});

describe("where a DHCPv6 server may sit", () => {
  const web = seg("web", 10500, "FW", [10600, 10700], true);
  const db = seg("db", 10600, null, [10500], true);
  const app = seg("app", 10700, null, [10500], false);
  const fw = (ipv6: boolean): Service => ({
    name: "FW", device: "leaf1", port: "10GE1/0/4", vlan: 3000, address: "10.255.0.1/30", next_hop: "10.255.0.2",
    asn: null, kind: "firewall", ipv6: ipv6 ? { address: "fd00:255::1/64", next_hop: "fd00:255::2" } : null,
  });
  it("only peered segments with IPv6, behind the firewall only when it has IPv6", () => {
    expect(ipv6Places(web, [web, db, app], false, [fw(false)]).map((p) => p.value)).toEqual(["", "segment:10600"]);
    expect(ipv6Places(web, [web, db, app], false, [fw(true)]).map((p) => p.value)).toContain("service:FW");
  });
});
