import { describe, expect, it } from "vitest";
import type { Topology } from "./api";
import { bundleLoad, layout, mapLabel, naturalCompare, pairs, problemCount, flowSeconds } from "./topology";

// Shape of GET /topology?site=example on the real bench.
const example: Topology = {
  telemetry: { ok: true, error: null },
  nodes: [
    { name: "leaf1", site: "example", role: "leaf", status: "active", in_netbox: true, collected: true },
    { name: "spine1", site: "example", role: "spine", status: "planned", in_netbox: true, collected: false },
    { name: "server9", site: null, role: null, status: null, in_netbox: false, collected: false },
  ],
  links: [
    {
      source: "netbox",
      status: "missing",
      ends: [
        { device: "leaf1", interface: "100GE1/0/1", oper_up: false, lldp: [], check: "missing" },
        { device: "spine1", interface: "100GE1/0/1", oper_up: null, lldp: [], check: "unverified" },
      ],
    },
    {
      source: "lldp",
      status: "unexpected",
      ends: [
        {
          device: "leaf1",
          interface: "10GE1/0/7",
          oper_up: true,
          lldp: [{ system_name: "server9", port_id: "eth0" }],
          check: "unexpected",
        },
        { device: "server9", interface: "eth0", oper_up: null, lldp: [], check: "unverified" },
      ],
    },
  ],
};

const link = (a: string, ai: string, b: string, bi: string, status: Topology["links"][0]["status"] = "ok"): Topology["links"][0] => ({
  source: "netbox", status,
  ends: [
    { device: a, interface: ai, oper_up: true, lldp: [], check: "ok" },
    { device: b, interface: bi, oper_up: true, lldp: [], check: "ok" },
  ],
});
const node = (name: string, role: string, site = "east") => ({ name, site, role, status: "active", in_netbox: true, collected: true });

// east in small: a spine gateway pair, two leaf pairs (peer-links of two cables each).
const east: Topology = {
  telemetry: { ok: true, error: null },
  nodes: [node("east-spine2", "spine"), node("east-spine1", "spine"), node("east-leaf10", "leaf"), node("east-leaf9", "leaf"),
    node("east-leaf2", "leaf"), node("east-leaf1", "leaf")],
  links: [
    link("east-spine1", "10GE1/0/45", "east-spine2", "10GE1/0/45"),
    link("east-leaf1", "10GE1/0/45", "east-leaf2", "10GE1/0/45"), link("east-leaf1", "10GE1/0/46", "east-leaf2", "10GE1/0/46"),
    link("east-leaf9", "10GE1/0/45", "east-leaf10", "10GE1/0/45"),
    link("east-leaf1", "10GE1/0/43", "east-spine1", "10GE1/0/1"), link("east-leaf1", "10GE1/0/44", "east-spine2", "10GE1/0/1", "missing"),
  ],
};

describe("layout", () => {
  it("orders names naturally", () => {
    expect(["east-leaf10", "east-leaf9", "east-leaf2"].sort(naturalCompare)).toEqual(["east-leaf2", "east-leaf9", "east-leaf10"]);
  });

  it("finds pairs from the cables between them", () => {
    expect(pairs(["east-leaf1", "east-leaf2", "east-leaf9", "east-leaf10", "x"], east.links)).toEqual([
      ["east-leaf1", "east-leaf2"], ["east-leaf9", "east-leaf10"], ["x"]]);
  });

  it("puts spines above leaves, pairs on floors, the gateway pair on its own", () => {
    const map = layout(east, ["east-spine1", "east-spine2"]);
    const at = (name: string) => map.nodes.find((n) => n.name === name)!;
    expect(at("east-spine1").y).toBeLessThan(at("east-leaf1").y);
    expect(at("east-spine1").x).toBeLessThan(at("east-spine2").x);
    expect(at("east-leaf2").x).toBeLessThan(at("east-leaf9").x);     // pairs in natural order
    expect(map.floors.map((f) => [f.label, f.gateway])).toEqual([
      ["Gateway pair · M-LAG", true], ["pair 1", false], ["pair 2", false]]);
  });

  it("draws parallel cables once, with the worst state", () => {
    const map = layout(east);
    const peer = map.bundles.find((b) => b.id === "east-leaf1 -- east-leaf2")!;
    expect(peer.links).toHaveLength(2);
    expect(peer.peer).toBe(true);
    expect(map.bundles.find((b) => b.id === "east-leaf1 -- east-spine2")!.status).toBe("missing");
    expect(map.floors[0].label).toBe("Spine pair · M-LAG");          // no gateway pair given
  });

  it("puts LLDP-only neighbours on their own row and counts problems", () => {
    const map = layout(example);
    expect(map.nodes.find((n) => n.name === "server9")!.kind).toBe("other");
    expect(map.nodes.find((n) => n.name === "server9")!.y).toBeGreaterThan(map.nodes.find((n) => n.name === "leaf1")!.y);
    expect(problemCount(example)).toBe(2);
  });
});

describe("bundleLoad", () => {
  it("takes the busiest end of any cable, as a share of its speed", () => {
    const bundle = layout(east).bundles.find((b) => b.id === "east-leaf1 -- east-leaf2")!;
    expect(bundleLoad(bundle, {
      "east-leaf1": { "10GE1/0/45": { in_bps: 1e9, out_bps: 2.5e9 } },
      "east-leaf2": { "10GE1/0/46": { in_bps: 5e9 } },
    })).toEqual({ load: 0.5, bps: 5e9 });
    expect(bundleLoad(bundle, {})).toEqual({ load: null, bps: null });
  });
});

describe("traffic dots", () => {
  it("run faster on busier links", () => {
    expect(flowSeconds(0)).toBe(4.5);
    expect(flowSeconds(1)).toBe(1);
    expect(flowSeconds(0.5)).toBeLessThan(flowSeconds(0.1));
    expect(flowSeconds(7)).toBe(1);
  });
});


describe("mapLabel", () => {
  it("drops the fabric prefix of a name too long for a pair's gap, keeps short ones", () => {
    expect(mapLabel({ name: "lab-ensp-leaf1", site: "lab-ensp" })).toBe("leaf1");
    expect(mapLabel({ name: "east-leaf10", site: "east" })).toBe("east-leaf10");
    expect(mapLabel({ name: "border-leaf-long1", site: "lab" })).toBe("border-leaf-long1");
    // Two boxes called spine1 would be ambiguous: keep the prefix where the short name is taken.
    expect(mapLabel({ name: "example-spine1", site: "example" }, new Set(["spine1", "example-spine1"]))).toBe("example-spine1");
  });
});
