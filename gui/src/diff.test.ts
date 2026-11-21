import { describe, expect, it } from "vitest";
import { diffLines, diffStats, jobHref, jobLabel, patchLines, patchStats } from "./diff";

describe("diffLines", () => {
  it("classifies added, removed and context lines, including indented ones", () => {
    const text = [
      "- sysname ref-leaf1",
      "+ sysname leaf1",
      "  interface 100GE1/0/1",
      "  + mtu 9216",
      "  - mtu 1500",
      "",
    ].join("\n");

    expect(diffLines(text).map((l) => l.kind)).toEqual(["remove", "add", "context", "add", "remove"]);
  });

  it("has no lines for an empty diff", () => {
    expect(diffLines("")).toEqual([]);
  });
});

describe("jobHref", () => {
  it("links a diff to the job that recorded it", () => {
    expect(jobHref("drift", 7)).toBe("#/changes/checks/7");
    expect(jobHref("diff", 7)).toBe("#/changes/checks/7");
    expect(jobHref("deploy", 9)).toBe("#/changes/deploys/9");
    expect(jobLabel("deploy", 9)).toBe("deploy #9");
    expect(jobLabel(null, 7)).toBe("#7");
  });
});

// A patch as the example's pair Vlanif removal showed it.
const PATCH = `undo interface Vlanif3005
interface Eth-Trunk1
  undo port trunk allow-pass vlan all
  undo port link-type
  quit
interface Vlanif3006
  ip address 10.100.106.1 255.255.255.248
  mac-address 0201-0a64-6a03
  quit
vlan batch 3007
`;

describe("patchLines", () => {
  it("tells removals, additions, block headers and navigation apart", () => {
    expect(patchLines(PATCH).map((l) => l.kind)).toEqual([
      "remove", "context", "remove", "remove", "nav", "context", "add", "add", "nav", "add"]);
  });
  it("counts what a patch adds and removes", () => {
    expect(patchStats(PATCH)).toEqual({ added: 3, removed: 3 });
    expect(patchStats(null)).toEqual({ added: 0, removed: 0 });
  });
});

describe("diffStats", () => {
  it("counts + and - lines", () => {
    expect(diffStats("  interface 10GE1/0/3\n+   description web\n-   description old\n+ ntp unicast-server 1.2.3.4\n")).toEqual({ added: 2, removed: 1 });
    expect(diffStats(null)).toEqual({ added: 0, removed: 0 });
  });
});
