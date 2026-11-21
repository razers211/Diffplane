import { describe, expect, it } from "vitest";
import { effects, protectedRows, switchLines, tripsOf } from "./protection";

const plan = { storm: { broadcast: 1, multicast: 1, "unknown-unicast": 5 }, templates: { server: { edge: true }, hypervisor: { edge: false } }, recovery: 45 };

describe("port protection", () => {
  it("writes the generator's lines: storm control on a trunk's members, the rest on the trunk", () => {
    const lines = switchLines("on", plan, { "Eth-Trunk1": "hypervisor", "10GE1/0/10": "server" }, { "Eth-Trunk1": ["10GE1/0/1", "10GE1/0/2"] });
    expect(lines.slice(0, 4)).toEqual(["stp bpdu-protection", "error-down auto-recovery cause bpdu-protection interval 45",
      "error-down auto-recovery cause loopback-detect interval 45", "error-down auto-recovery cause storm-control interval 45"]);
    expect(lines).toContain(" storm control unknown-unicast min-rate percent 5 max-rate percent 5");
    const trunk = lines.indexOf("interface Eth-Trunk1");
    expect(lines.slice(trunk + 1, trunk + 4)).toEqual([" loopback-detect enable", " loopback-detect action error-down", " loopback-detect recovery-time 45"]);
    expect(lines.indexOf("interface 10GE1/0/1")).toBeLessThan(trunk);
    expect(lines.filter((l) => l === " stp edged-port enable")).toHaveLength(1);   // the Server port only
  });

  it("Watch logs only; Off writes nothing", () => {
    const lines = switchLines("watch", plan, { "10GE1/0/10": "server" }, {});
    expect(lines).not.toContain("stp bpdu-protection");
    expect(lines).not.toContain(" storm control action block");
    expect(lines.some((l) => l.includes("action error-down"))).toBe(false);
    expect(switchLines("off", plan, { "10GE1/0/10": "server" }, {})).toEqual([]);
  });

  it("says what a port does in plain words", () => {
    expect(effects("on", "server", plan)).toHaveLength(3);
    expect(effects("on", "hypervisor", plan)).toHaveLength(2);
    expect(effects("watch", "server", plan)[1]).toContain("logged");
    expect(effects("off", "server", plan)).toEqual([]);
  });

  it("lists protected ports switch by switch in port order", () => {
    expect(protectedRows({ b: { "10GE1/0/10": "server", "10GE1/0/9": "hypervisor" }, a: { "Eth-Trunk1": "server" } }).map((r) => `${r.device} ${r.port}`))
      .toEqual(["a Eth-Trunk1", "b 10GE1/0/9", "b 10GE1/0/10"]);
  });
});

describe("what protection does now", () => {
  it("finds a port's trips, a trunk's on its members", () => {
    const trip = (port: string) => ({ device: "leaf1", port, cause: "storm-control", state: "block", detail: "broadcast", reopen: null, text: "" });
    const all = [trip("10GE1/0/1"), trip("10GE1/0/9"), { ...trip("10GE1/0/1"), device: "leaf2" }];
    expect(tripsOf(all, "leaf1", "Eth-Trunk1", ["10GE1/0/1", "10GE1/0/2"]).map((t) => t.port)).toEqual(["10GE1/0/1"]);
    expect(tripsOf(all, "leaf1", "10GE1/0/9")).toHaveLength(1);
    expect(tripsOf(all, "leaf1", "10GE1/0/3")).toEqual([]);
  });
});
