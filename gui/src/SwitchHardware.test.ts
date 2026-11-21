import { describe, expect, it } from "vitest";
import { outcome } from "./SwitchHardware";

const base = { device: "s1", model: "CE6881H-48T6CQ", version: "V300R025C00SPC500", patch: "", serial: "",
  was_model: "CE6881H-48S6CQ", added: [], retyped: [], removed: [], blocked: [], skipped: [], changed: false };

describe("reading a switch", () => {
  it("says what changed in plain words", () => {
    expect(outcome({ ...base, changed: true, retyped: Array(48).fill("p") }))
      .toBe("Updated: model CE6881H-48S6CQ → CE6881H-48T6CQ, 48 port types corrected.");
  });
  it("says nothing changed and why when a port in use would vanish", () => {
    expect(outcome({ ...base, blocked: ["10GE1/0/1"] })).toMatch(/^Nothing changed: the switch has no 10GE1\/0\/1, which is in use/);
  });
  it("says when all matches", () => {
    expect(outcome({ ...base, was_model: base.model })).toBe("Everything already matches the switch.");
  });
});
