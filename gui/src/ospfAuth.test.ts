import { describe, expect, it } from "vitest";
import { nextStep } from "./ospfAuth";

const state = (stage: "off" | "on" | "added" | "moved") => ({ enabled: stage !== "off", keys: [1], send: 1, stage });

describe("link authentication", () => {
  it("offers the key change's steps in order, none while off", () => {
    expect(nextStep(state("off"))).toBeNull();
    expect((["on", "added", "moved"] as const).map((s) => nextStep(state(s))?.action)).toEqual(["add", "send", "retire"]);
  });
});
