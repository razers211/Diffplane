import { describe, expect, it } from "vitest";
import { emptySettings, settingsProblem, slugOf, toSettings, type SettingsDraft } from "./newFabric";

const ready: SettingsDraft = {
  ...emptySettings(), name: "DC 2", asn: "65002", gatewayMac: "0200-00fa-0002",
  prefixes: { loopback: "10.92.0.0/24", vtep: "10.92.1.0/24", p2p: "10.92.2.0/24", mlag_bypass: "10.92.3.0/24", pip: "10.92.4.0/24", gateways: "10.92.128.0/17" },
};

describe("new fabric settings", () => {
  it("accepts a complete draft and sends it", () => {
    expect(settingsProblem(ready, ["example"])).toBeNull();
    expect(slugOf("DC 2")).toBe("dc-2");
    const body = toSettings({ ...ready, ntpServers: "192.0.2.1, 192.0.2.2", ntpVpn: "_management_vpn_" });
    expect(body).toMatchObject({ asn: 65002, bypass_vlan: 4093, ntp_servers: ["192.0.2.1", "192.0.2.2"],
      ntp_vpn_instance: "_management_vpn_", snmp_location: null });
    expect(toSettings({ ...ready, ntpVpn: "_management_vpn_" }).ntp_vpn_instance).toBeNull();   // no servers
  });

  it("refuses what the orchestrator would refuse", () => {
    expect(settingsProblem({ ...ready, name: "example" }, ["example"])).toMatch(/already exists/);
    expect(settingsProblem({ ...ready, name: "Inventory" }, [])).toMatch(/reserved/);
    expect(settingsProblem({ ...ready, asn: "4200000101" }, [])).toMatch(/2-byte ASN/);
    expect(settingsProblem({ ...ready, asn: "4200000101", architecture: "centralized" }, [])).toBeNull();
    expect(settingsProblem({ ...ready, prefixes: null }, [])).toMatch(/address plan/);
    expect(settingsProblem({ ...ready, prefixes: { ...ready.prefixes!, p2p: "10.92.2.0" } }, [])).toMatch(/Point-to-point/);
    expect(settingsProblem({ ...ready, gatewayMac: "02:00:5b:01:00:02" }, [])).toMatch(/Gateway MAC/);
  });
});
