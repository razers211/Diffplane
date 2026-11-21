import { describe, expect, it } from "vitest";
import type { Flows, Topology } from "./api";
import { alertLabel, axisBps, edgeIn, keep, nextFabric, niceMax, talkers } from "./noc";

describe("NOC", () => {
  it("counts edge ports only: no fabric links, no MEth, no trunks", () => {
    const topology = { nodes: [], telemetry: { ok: true, error: null }, links: [
      { source: "netbox", status: "up", ends: [{ device: "rack1-leaf1", interface: "100GE1/0/1" }, { device: "gw-spine1", interface: "100GE1/0/3" }] },
    ] } as unknown as Topology;
    const traffic = {
      "rack1-leaf1": { "100GE1/0/1": { in_bps: 9e9 }, "10GE1/0/5": { in_bps: 2e9 }, "MEth0/0/0": { in_bps: 1e6 }, "Eth-Trunk2": { in_bps: 5e9 } },
      "gw-spine1": { "100GE1/0/3": { in_bps: 8e9 }, "10GE1/0/41": { in_bps: 1e9, out_bps: 3e9 } },
    };
    expect(edgeIn(traffic, topology)).toBe(3e9);
    expect(edgeIn({}, null)).toBe(0);
  });

  it("keeps two minutes of samples", () => {
    const samples = [{ t: 0, bps: 1 }, { t: 60_000, bps: 2 }];
    expect(keep(samples, { t: 125_000, bps: 3 })).toEqual([{ t: 60_000, bps: 2 }, { t: 125_000, bps: 3 }]);
  });

  it("rotates through the fabrics", () => {
    expect(nextFabric(["site-a", "example"], "site-a")).toBe("example");
    expect(nextFabric(["site-a", "example"], "example")).toBe("site-a");
    expect(nextFabric(["site-a", "example"], "")).toBe("site-a");
    expect(nextFabric([], "example")).toBe("example");
  });

  it("names the busiest segment pairs, the outside for no segment", () => {
    const flows = { segments: [
      { src_segment: "APP-WEB", dst_segment: "APP-DB", bps: 2e9 },
      { src_segment: "OOB-HOSTS", dst_segment: "", bps: 4e8 },
      { src_segment: "A", dst_segment: "B", bps: 0 },
    ] } as unknown as Flows;
    expect(talkers(flows)).toEqual([{ from: "APP-WEB", to: "APP-DB", bps: 2e9 }, { from: "OOB-HOSTS", to: "outside", bps: 4e8 }]);
    expect(talkers(null)).toEqual([]);
  });

  it("rounds the axis up", () => {
    expect(niceMax(6.2e9)).toBe(1e10);
    expect(niceMax(1.4e9)).toBe(2e9);
    expect(niceMax(0)).toBe(1);
  });

  it("names alerts in plain words and labels the axis short", () => {
    expect(alertLabel("FabricBgpPeerDown")).toBe("BGP peer down");
    expect(alertLabel("SomethingNew")).toBe("SomethingNew");
    expect([axisBps(1e10), axisBps(5e9), axisBps(2.5e8), axisBps(0)]).toEqual(["10G", "5G", "250M", "0"]);
  });
});
