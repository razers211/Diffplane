import { describe, expect, it } from "vitest";
import type { DeviceTrunks } from "./api";
import { candidatePorts, deleteBlocker, trunkProblem } from "./trunkForm";

const example: DeviceTrunks = {
  device: "leaf1",
  peer: "leaf2",
  trunks: [
    { name: "Eth-Trunk0", number: 0, members: ["100GE1/0/5"], kind: "peer-link", segments: 0, service: false },
    { name: "Eth-Trunk1", number: 1, members: ["10GE1/0/1"], kind: "m-lag", segments: 2, service: false },
  ],
  free_ports: ["10GE1/0/3", "10GE1/0/4", "10GE1/0/5"],
  pair_free_ports: ["10GE1/0/3", "10GE1/0/5"],
  next_number: 2,
};

describe("new trunk form", () => {
  it("offers ports free on both devices for an M-LAG trunk", () => {
    expect(candidatePorts(example, false)).toEqual(["10GE1/0/3", "10GE1/0/4", "10GE1/0/5"]);
    expect(candidatePorts(example, true)).toEqual(["10GE1/0/3", "10GE1/0/5"]);
    expect(candidatePorts({ ...example, peer: null, pair_free_ports: null }, true)).toEqual([]);
  });

  it("explains what blocks sending", () => {
    expect(trunkProblem(example, "2", ["10GE1/0/3"], true)).toBeNull();
    expect(trunkProblem(example, "0", ["10GE1/0/3"], false)).toMatch(/1–1023/);
    expect(trunkProblem(example, "x", ["10GE1/0/3"], false)).toMatch(/1–1023/);
    expect(trunkProblem(example, "1", ["10GE1/0/3"], false)).toMatch(/already exists/);
    expect(trunkProblem(example, "2", [], false)).toMatch(/at least one/);
    expect(trunkProblem(example, "2", ["10GE1/0/4"], true)).toBe("Not free on both devices: 10GE1/0/4");
    expect(trunkProblem({ ...example, peer: null }, "2", ["10GE1/0/3"], true)).toMatch(/not in an M-LAG pair/);
  });

  it("says why a trunk cannot be deleted", () => {
    expect(deleteBlocker(example.trunks[0])).toMatch(/fabric YAML/);
    expect(deleteBlocker(example.trunks[1])).toMatch(/2 segments/);
    expect(deleteBlocker({ kind: "single", segments: 0, service: true })).toMatch(/service/);
    expect(deleteBlocker({ kind: "single", segments: 0, service: false })).toBeNull();
  });
});
