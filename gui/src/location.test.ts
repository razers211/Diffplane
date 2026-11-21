import { describe, expect, it } from "vitest";
import { freePositions, placeLabel } from "./location";

describe("location", () => {
  it("labels a placement", () => {
    expect(placeLabel({ location: "Hall A", rack: "R01", position: 40, face: "front" })).toBe("Hall A / R01 · U40 front");
    expect(placeLabel({ location: "Hall A", rack: "R01", position: null, face: null })).toBe("Hall A / R01");
    expect(placeLabel({ location: null, rack: null, position: null, face: null })).toBe("not placed");
  });

  it("lists free U positions, top first, without the device itself", () => {
    const rack = { name: "R01", u_height: 5, devices: [
      { name: "a", position: 4, face: "front" as const, u_height: 2 },
      { name: "me", position: 1, face: "front" as const, u_height: 1 },
      { name: "loose", position: null, face: null, u_height: 1 },
    ] };
    expect(freePositions(rack, 1, "me")).toEqual([3, 2, 1]);
    expect(freePositions(rack, 2, "me")).toEqual([2, 1]);
    expect(freePositions(rack, 1, "other")).toEqual([3, 2]);
  });
});
