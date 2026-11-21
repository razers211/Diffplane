import { describe, expect, it } from "vitest";
import { planRows, portRows } from "./qos";

describe("QoS", () => {
  it("shows the plan with strict queues first and each weighted queue's share", () => {
    const rows = planRows({ strict: [5, 6, 7], weights: { 0: 30, 1: 5, 2: 15, 3: 35, 4: 15 }, marks: { storage: "af31", scavenger: "cs1" } });
    expect(rows.map((r) => r.queue)).toEqual([7, 6, 5, 4, 3, 2, 1, 0]);
    expect(rows.slice(0, 3).every((r) => r.mode === "PQ" && r.share === null)).toBe(true);
    expect(rows.find((r) => r.queue === 3)).toMatchObject({ mode: "DRR", weight: 35, share: 35 });
  });

  it("lists port marks by switch then port", () => {
    expect(portRows({ b: { "10GE1/0/10": "storage", "10GE1/0/9": "scavenger" }, a: { "Eth-Trunk1": "storage" } })
      .map((r) => `${r.device} ${r.port}`)).toEqual(["a Eth-Trunk1", "b 10GE1/0/9", "b 10GE1/0/10"]);
  });
});
