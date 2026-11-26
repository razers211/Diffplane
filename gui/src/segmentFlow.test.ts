import { describe, expect, it } from "vitest";
import type { Segment } from "./api";
import { emptyDraft, followingVni, gatewayPlace, gatewaySwitches, nameError, plan, portErrors, routingErrors, segmentErrors, segmentVpn, suggestedVni } from "./segmentFlow";

const web: Segment = { fabric: "example", name: "web", vni: 10500, bd_id: 500, routing: null, attachments: [] };

describe("segment step", () => {
  it("accepts a fresh segment and suggests the VNI", () => {
    expect(segmentErrors({ ...emptyDraft(), name: "db", vni: "10600", bd: "600" }, [web])).toEqual({});
    expect(suggestedVni(600)).toBe(10600);
  });
  it("catches taken names, VNIs and bridge domains", () => {
    const errors = segmentErrors({ ...emptyDraft(), name: "web", vni: "10500", bd: "500" }, [web]);
    expect(errors.name).toMatch(/already exists/);
    expect(errors.vni).toMatch(/taken/);
    expect(errors.bd).toMatch(/taken/);
  });
  it("checks forms and ranges", () => {
    const errors = segmentErrors({ ...emptyDraft(), name: "a:b", vni: "15777216", bd: "x" }, []);
    expect(errors.name).toMatch(/only letters, digits/);
    expect(errors.vni).toMatch(/1 to 15777215/);
    expect(errors.bd).toMatch(/1 to 16777215/);
  });
});

describe("ports and routing steps", () => {
  it("checks VLAN IDs and duplicates", () => {
    const d = { ...emptyDraft(), ports: [
      { device: "leaf1", port: "Eth-Trunk1", trunk: true, vid: "500" },
      { device: "leaf1", port: "10GE1/0/3", trunk: false, vid: "5000" },
      { device: "leaf1", port: "Eth-Trunk1", trunk: true, vid: "" },
    ] };
    expect(portErrors(d)).toEqual([null, "VLAN ID must be 1-4094", "leaf1 Eth-Trunk1 is listed twice"]);
  });
  it("checks the prefix length only when routed", () => {
    expect(routingErrors({ ...emptyDraft(), prefixLength: "31" })).toBeNull();
    expect(routingErrors({ ...emptyDraft(), routed: true, prefixLength: "31" })).toMatch(/8 to 30/);
    expect(routingErrors({ ...emptyDraft(), routed: true, subnet: "10.20.30.0/24" })).toBeNull();
    expect(routingErrors({ ...emptyDraft(), routed: true, subnet: "10.20.30.5/24" })).toMatch(/boundary/);
    expect(routingErrors({ ...emptyDraft(), routed: true, subnet: "10.20.30.0/31" })).toMatch(/8 to \/30/);
    expect(routingErrors({ ...emptyDraft(), routed: true, subnet: "10.20.300.0/24" })).toMatch(/IPv4/);
  });
});

describe("plan", () => {
  it("creates, attaches, then routes", () => {
    const d = { ...emptyDraft(), name: "db", vni: "10600", bd: "600", routed: true, firewall: "FW", peers: [10500],
      ports: [{ device: "leaf1", port: "Eth-Trunk1", trunk: true, vid: "600" }] };
    expect(plan(d).map((w) => w.kind)).toEqual(["create", "attach", "route"]);
    expect(plan(d)[1].label).toBe("Attach to leaf1 Eth-Trunk1 (both leaves of the pair), VLAN 600");
    expect(plan(d)[2].label).toBe("Route it: a /24 gateway, default route via FW, peered with 1 segment");
    expect(plan({ ...d, routed: false, ports: [] }).map((w) => w.kind)).toEqual(["create"]);
    // relay servers in the segment's own VPN instance, after routing
    const relayed = plan({ ...d, relay: "10.100.0.50, 10.100.0.51" });
    expect(relayed.map((w) => w.kind)).toEqual(["create", "attach", "route", "relay"]);
    expect(relayed[3].label).toBe("Relay DHCP to 10.100.0.50, 10.100.0.51");
    // IPv6 after routing, before relay
    expect(plan({ ...d, ipv6: "slaac", relay: "10.100.0.50" }).map((w) => w.kind)).toEqual(["create", "attach", "route", "ipv6", "relay"]);
    expect(routingErrors({ ...d, relay: "dhcp.example.com" })).toBe("DHCP server dhcp.example.com is not an IPv4 address");
  });
  it("says where the gateway lives", () => {
    const d = { ...emptyDraft(), ports: [{ device: "leaf1", port: "10GE1/0/3", trunk: false, vid: "" }] };
    expect(gatewayPlace("centralized", ["s1", "s2"], d)).toBe("on the gateway pair s1 + s2");
    expect(gatewayPlace("symmetric", null, d)).toBe("on every leaf it is attached to (leaf1)");
  });
});

describe("segment names (they name the VPN instance <name>-<bd>)", () => {
  it("accepts letters, digits, - and _ starting with a letter", () => {
    expect(nameError("Web_Tier-2", 500)).toBeNull();
    expect(nameError("2web", 500)).toMatch(/Start with a letter/);
    expect(nameError("web tier", 500)).toMatch(/only letters/);
  });

  it("keeps <name>-<bd> within VRP's limit", () => {
    expect(nameError("a".repeat(27), 500)).toBeNull();   // 31 characters
    expect(nameError("a".repeat(28), 500)).toMatch(/Too long: .* 32 characters/);
    expect(segmentVpn("web", 500)).toBe("web-500");
  });
});

describe("the routing diagram's switches", () => {
  it("shows the gateway pair, or the leaves the segment is attached to", () => {
    const d = { ...emptyDraft(), ports: [
      { device: "leaf1", port: "10GE1/0/3", trunk: false, vid: "" },
      { device: "leaf1", port: "10GE1/0/7", trunk: false, vid: "" },
      { device: "leaf3", port: "Eth-Trunk1", trunk: true, vid: "" },
    ] };
    expect(gatewaySwitches("symmetric", null, d)).toEqual(["leaf1", "leaf3"]);
    expect(gatewaySwitches("centralized", ["c-gw1", "c-gw2"], d)).toEqual(["c-gw1", "c-gw2"]);
  });
});

describe("the VNI follows the bridge domain", () => {
  it("is 10000 + BD, and empty while the BD is cleared or not a number", () => {
    expect(followingVni("21")).toBe("10021");
    expect(followingVni("")).toBe("");
    expect(followingVni("2x")).toBe("");
    expect(followingVni(" 700 ")).toBe("10700");
  });
});

