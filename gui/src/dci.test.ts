import { describe, expect, it } from "vitest";
import { commonSegments, uplinkOf, waiting, type DciLink } from "./dci";

const link: DciLink = { name: "dc", split_group: "DCI", segments: [],
  sides: [{ fabric: "a", pair: ["a1", "a2"], uplinks: {} }, { fabric: "b", pair: ["b1", "b2"], uplinks: {} }] };

describe("DC links", () => {
  it("waits for active borders not verified with the link", () => {
    expect(waiting(link, ["a1"], new Set(["a1", "a2", "b1"]))).toEqual(["a2", "b1"]);
    expect(waiting(link, [], new Set())).toEqual([]);   // all planned: nothing to wait for
  });

  it("offers the segments both fabrics have by name", () => {
    expect(commonSegments([{ name: "web", vni: 10500 }, { name: "db", vni: 10600 }], [{ name: "web", vni: 20500 }]))
      .toEqual([{ name: "web", a: 10500, b: 20500 }]);
  });

  it("reads an uplink row", () => {
    expect(uplinkOf({ port: "", address: "", neighbour: "", asn: "" })).toEqual([]);
    expect(uplinkOf({ port: "100GE1/0/3", address: "10.0.0.1/30", neighbour: "10.0.0.2", asn: "65002" }))
      .toEqual([{ port: "100GE1/0/3", address: "10.0.0.1/30", neighbour: "10.0.0.2", asn: 65002 }]);
    expect(uplinkOf({ port: "100GE1/0/3", address: "10.0.0.1/30", neighbour: "10.0.0.2", asn: "x" })).toMatch(/ASN/);
  });
});
