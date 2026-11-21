import { describe, expect, it } from "vitest";
import type { Segment } from "./api";
import { doorOf, routingBody } from "./exits";

const routed = { name: "web", vni: 10500, routing: { gateway: "10.100.0.1/26", peers: [10600], firewall: "FW" } } as Segment;

describe("segment exits", () => {
  it("knows where a segment leaves", () => {
    expect(doorOf(routed)).toBe("FW");
    expect(doorOf({ ...routed, routing: { ...routed.routing!, firewall: null } })).toBeNull();
    expect(doorOf({ ...routed, routing: null })).toBeUndefined();
  });

  it("changes one thing and keeps the subnet length and the rest", () => {
    expect(routingBody(routed, { firewall: null })).toEqual({ routed: true, prefix_length: 26, firewall: null, peers: [10600] });
    expect(routingBody(routed, { peers: [10600, 10700] })).toEqual({ routed: true, prefix_length: 26, firewall: "FW", peers: [10600, 10700] });
    expect(routingBody({ ...routed, routing: null }, { firewall: "FW" })).toEqual({ routed: true, prefix_length: 24, firewall: "FW", peers: [] });
  });
});
