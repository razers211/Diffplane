import { describe, expect, it } from "vitest";
import { attachableLeaves, serviceLeaves } from "./placement";

const devices = [
  { name: "leaf1", role: "leaf" },
  { name: "border1", role: "border-leaf" },
  { name: "spine1", role: "spine" },
];
const names = (list: { name: string }[]) => list.map((d) => d.name);

describe("where segments and services go", () => {
  it("keeps border leaves for routing in a symmetric fabric", () => {
    expect(names(attachableLeaves(devices, false))).toEqual(["leaf1"]);
    expect(names(serviceLeaves(devices, false))).toEqual(["border1"]);
  });

  it("offers every leaf when a symmetric fabric has no border leaf", () => {
    const plain = devices.filter((d) => d.role !== "border-leaf");
    expect(names(serviceLeaves(plain, false))).toEqual(["leaf1"]);
  });

  it("leaves a centralized fabric's gateway pair (border-leaf role) as it was", () => {
    expect(names(attachableLeaves(devices, true))).toEqual(["leaf1", "border1"]);
    expect(names(serviceLeaves(devices, true))).toEqual(["leaf1", "border1"]);
  });
});
