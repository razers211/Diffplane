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
    expect(names(serviceLeaves(devices, null))).toEqual(["border1"]);
  });

  it("offers every leaf when a symmetric fabric has no border leaf", () => {
    const plain = devices.filter((d) => d.role !== "border-leaf");
    expect(names(serviceLeaves(plain, null))).toEqual(["leaf1"]);
  });

  it("leaves a centralized fabric's gateway pair (border-leaf role) as it was", () => {
    expect(names(attachableLeaves(devices, true))).toEqual(["leaf1", "border1"]);
  });

  it("puts a centralized fabric's services on its gateway pair, spines included", () => {
    const spines = [...devices, { name: "spine2", role: "spine" }];
    expect(names(serviceLeaves(spines, ["spine1", "spine2"]))).toEqual(["spine1", "spine2"]);
    expect(names(serviceLeaves(devices, ["border1"]))).toEqual(["border1"]);
  });
});
