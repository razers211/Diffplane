import { describe, expect, it } from "vitest";
import type { Reach, ReachCheck } from "./api";
import { checkResult, drawnPath, fixLabel, traffic } from "./reach";

const stop = (over: Partial<ReachCheck>): ReachCheck =>
  ({ check: "security", ok: false, text: "", from: "", switch: "b", page: null, direction: "there", ...over });
const answer = (s: ReachCheck | null) => ({ stop: s, switches: ["a", "b", "c"] }) as Reach;

describe("reachability", () => {
  it("turns the chips into a protocol and port", () => {
    expect(traffic("Anything", "tcp", "80")).toEqual({});
    expect(traffic("Web", "", "")).toEqual({ proto: "tcp", port: 443 });
    expect(traffic("Ping", "", "")).toEqual({ proto: "icmp" });
    expect(traffic("Expert", "udp", "53")).toEqual({ proto: "udp", port: 53 });
    expect(traffic("Expert", "any", "53")).toEqual({});
  });

  it("refuses a port that cannot be", () => {
    expect(traffic("Expert", "icmp", "53")).toBe("Only TCP and UDP have ports.");
    expect(traffic("Expert", "tcp", "70000")).toBe("A port is a number from 1 to 65535.");
  });

  it("draws the path up to the stop, and all of it when the way back stops", () => {
    expect(drawnPath(answer(null))).toEqual({ path: ["a", "b", "c"] });
    expect(drawnPath(answer(stop({})))).toEqual({ path: ["a", "b"], stop: "b" });
    expect(drawnPath(answer(stop({ direction: "back" })))).toEqual({ path: ["a", "b", "c"], stop: "b" });
    expect(drawnPath(answer(stop({ switch: "z" })))).toEqual({ path: ["z"], stop: "z" });
  });

  it("names the page that fixes it", () => {
    expect(fixLabel("#/network/security")).toBe("Open Security groups");
    expect(fixLabel("#/switches/leaf1")).toBe("Open the switch");
    expect(fixLabel("#/changes")).toBe("Open Changes");
  });

  it("says what a check that could not run is", () => {
    expect(checkResult(stop({ ok: null }))).toBe("not known");
  });
});
