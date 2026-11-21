import { describe, expect, it } from "vitest";
import type { RibRoute } from "./api";
import { bgpInstances, choices, filterRoutes, page, pageCount } from "./routeView";

const route = (vpn: string, prefix: string, protocol: string, active = true): RibRoute => ({
  vpn, prefix, protocol, process: 0, preference: 0, cost: 0, next_hop: "127.0.0.1", interface: "LoopBack0", active,
  state: "", flags: "", age: "", tag: 0,
});
const routes = [route("_public_", "10.10.0.2/32", "direct"), route("_public_", "10.10.0.2/32", "ospf", false),
  route("FW", "198.51.100.0/24", "static"), route("_management_vpn_", "0.0.0.0/0", "static")];

describe("route view", () => {
  it("filters by instance, protocol, text and inactive routes", () => {
    const all = { vpn: "", protocol: "", text: "", inactive: true };
    expect(filterRoutes(routes, all)).toHaveLength(4);
    expect(filterRoutes(routes, { ...all, inactive: false })).toHaveLength(3);
    expect(filterRoutes(routes, { ...all, vpn: "FW" }).map((r) => r.prefix)).toEqual(["198.51.100.0/24"]);
    expect(filterRoutes(routes, { ...all, protocol: "static" })).toHaveLength(2);
    expect(filterRoutes(routes, { ...all, text: "198.51" })).toHaveLength(1);
  });

  it("pages by 100", () => {
    const many = Array.from({ length: 250 }, (_, i) => i);
    expect(pageCount(250)).toBe(3);
    expect(pageCount(0)).toBe(1);
    expect(page(many, 2)).toEqual([200, 201, 202, 203, 204, 205, 206, 207, 208, 209, ...many.slice(210)]);
  });

  it("offers the instances", () => {
    expect(choices(routes, "vpn")).toEqual(["FW", "_management_vpn_", "_public_"]);
    expect(bgpInstances(routes)).toEqual([null, "FW"]);
  });
});
