import { describe, expect, it } from "vitest";
import type { OverrideEntry } from "./api";
import { changedSwitches, grouped, whereItIs } from "./overrides";

const entry = (area: string, target: string | null, scope = "switch") =>
  ({ area, target, scope, path: "x", key: "x", text: "", derived: "", standard: true, value: 1 }) as OverrideEntry;

describe("found on import", () => {
  it("keeps the areas in order and drops the empty ones", () => {
    const shown = grouped([entry("Switch extras", "A"), entry("Underlay", null)]);
    expect(shown.map(([area]) => area)).toEqual(["Underlay", "Switch extras"]);
  });
  it("names where a value is kept", () => {
    expect(whereItIs(entry("Underlay", null, "fabric"))).toBe("The whole fabric");
    expect(whereItIs(entry("Segments", "100110", "segment"))).toBe("Segment VNI 100110");
    expect(whereItIs(entry("Switch extras", "rack1"))).toBe("rack1");
  });
  it("lists only the switches a patch changes", () => {
    expect(changedSwitches({ a: "", b: "undo x\n", c: "  \n" })).toEqual([["b", "undo x\n"]]);
  });
});
