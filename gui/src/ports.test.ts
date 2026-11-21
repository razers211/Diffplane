import { describe, expect, it } from "vitest";
import type { Port, Segment } from "./api";
import { alreadyOn, colourOf, presetVid, splitTarget, targetOf, untaggedBlocker } from "./ports";

const seg = (name: string, vni: number) => ({ name, vni, bd_id: vni - 10000 }) as Segment;
const segments = [seg("web", 10500), seg("db", 10600), seg("app", 10400)];

describe("ports & segments", () => {
  it("names a target by switch and port, trunks included", () => {
    expect(splitTarget(targetOf("rack1-leaf1", "Eth-Trunk2"))).toEqual({ device: "rack1-leaf1", iface: "Eth-Trunk2" });
  });

  it("proposes the bridge-domain ID as VLAN when it is one", () => {
    expect([presetVid(500), presetVid(4094), presetVid(5000)]).toEqual(["500", "4094", ""]);
  });

  it("allows one untagged segment per port", () => {
    const port = { name: "10GE1/0/3", segments: [{ vni: 10500, vid: null }, { vni: 10600, vid: 600 }] } as Port;
    expect(untaggedBlocker(port, segments)).toBe("already carries web untagged");
    expect(untaggedBlocker({ ...port, segments: [{ vni: 10600, vid: 600 }] }, segments)).toBeNull();
    expect(untaggedBlocker(undefined, segments)).toBeNull();
    expect(alreadyOn(port, 10600)).toBe(true);
    expect(alreadyOn(port, 10400)).toBe(false);
  });

  it("colours segments by VNI order, not by list order", () => {
    expect([colourOf(segments, 10400), colourOf(segments, 10500), colourOf(segments, 10600), colourOf(segments, 1)]).toEqual([1, 2, 3, 0]);
  });
});
