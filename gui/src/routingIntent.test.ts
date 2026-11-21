import { describe, expect, it } from "vitest";
import {
  count,
  keyOf,
  kindsSummary,
  names,
  parseAreas,
  parseNumber,
  putItem,
  putRule,
  removeItem,
  removeRule,
  shortError,
  targetLabel,
  type DeviceRouting,
} from "./routingIntent";

const base: DeviceRouting = {
  prefix_lists: { OUT: [{ index: 10, action: "permit", prefix: "10.0.0.0/8", ge: 16, le: 24 }] },
  static_routes: [{ prefix: "10.1.0.0/16", next_hop: "NULL0" }],
};

describe("named lists", () => {
  it("adds a rule in order and replaces the same index", () => {
    let r = putRule(base, "prefix_lists", "OUT", { index: 5, action: "deny", prefix: "0.0.0.0/0" });
    expect(r.prefix_lists?.OUT.map((x) => x.index)).toEqual([5, 10]);
    r = putRule(r, "prefix_lists", "OUT", { index: 10, action: "deny", prefix: "10.0.0.0/8" });
    expect(r.prefix_lists?.OUT).toHaveLength(2);
    expect(r.prefix_lists?.OUT[1].action).toBe("deny");
    expect(base.prefix_lists?.OUT).toHaveLength(1); // never mutated
  });
  it("creates a list and removes it with its last rule", () => {
    let r = putRule(base, "route_policies", "IN", { node: 10, action: "permit", match_ip_prefix: "OUT" });
    expect(names(r, "route_policies")).toEqual(["IN"]);
    r = removeRule(r, "route_policies", "IN", 10);
    expect(r.route_policies).toEqual({});
  });
});

describe("keyed objects", () => {
  it("uses the orchestrator's identities", () => {
    expect(keyOf("static_routes", { vpn: "FW", prefix: "10.0.0.0/8", next_hop: "NULL0" })).toBe("FW 10.0.0.0/8 NULL0");
    expect(keyOf("bgp_peers", { address: "192.0.2.1", asn: 65100 })).toBe(" 192.0.2.1");
    expect(keyOf("ospf", { process: 2, vpn: "FW", router_id: "1.1.1.1" })).toBe("2");
  });
  it("replaces the same object and removes by identity", () => {
    let r = putItem(base, "static_routes", { prefix: "10.1.0.0/16", next_hop: "NULL0", preference: 20 });
    expect(r.static_routes).toEqual([{ prefix: "10.1.0.0/16", next_hop: "NULL0", preference: 20 }]);
    r = putItem(r, "bgp_peers", { address: "192.0.2.1", asn: 65100 });
    expect(count(r)).toBe(3);
    r = removeItem(r, "static_routes", " 10.1.0.0/16 NULL0");
    expect(r.static_routes).toEqual([]);
  });
});

describe("fields", () => {
  it("parses integers", () => {
    expect(parseNumber("", "Index", 1, 10, true)).toEqual({ ok: true, value: null });
    expect(parseNumber("", "Index", 1, 10).ok).toBe(false);
    expect(parseNumber("7", "Index", 1, 10)).toEqual({ ok: true, value: 7 });
    expect(parseNumber("11", "Index", 1, 10).ok).toBe(false);
    expect(parseNumber("1.5", "Index", 1, 10).ok).toBe(false);
  });
  it("parses areas as VRP writes them", () => {
    expect(parseAreas("0, 0.0.0.1 256")).toEqual({ ok: true, value: ["0.0.0.0", "0.0.0.1", "0.0.1.0"] });
    expect(parseAreas("").ok).toBe(true);
    expect(parseAreas("1.2.3").ok).toBe(false);
  });
  it("labels pairs", () => {
    expect(targetLabel("a+b")).toBe("a + b");
    expect(targetLabel("spine1")).toBe("spine1");
  });
});

describe("list summaries", () => {
  it("counts what a target holds", () => {
    expect(kindsSummary(base)).toEqual(["1 prefix list", "1 static route"]);
    expect(kindsSummary(undefined)).toEqual([]);
  });
  it("keeps the line that says why an import failed", () => {
    const tb = "AnnetError: annet show failed: Traceback\n  File \"x.py\", line 3, in main\nConnectionRefusedError: [Errno 111] Connection refused";
    expect(shortError(tb)).toBe("ConnectionRefusedError: [Errno 111] Connection refused");
    expect(shortError("plain")).toBe("plain");
  });
});
