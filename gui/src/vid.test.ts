import { describe, expect, it } from "vitest";
import { parseVid, vidLabel } from "./vid";

describe("parseVid", () => {
  it("treats empty as untagged", () => {
    expect(parseVid("")).toEqual({ ok: true, vid: null });
    expect(parseVid(" untagged ")).toEqual({ ok: true, vid: null });
  });
  it("accepts 1-4094", () => {
    expect(parseVid("500")).toEqual({ ok: true, vid: 500 });
    expect(parseVid("4094")).toEqual({ ok: true, vid: 4094 });
  });
  it("refuses the rest", () => {
    expect(parseVid("0").ok).toBe(false);
    expect(parseVid("4095").ok).toBe(false);
    expect(parseVid("5a").ok).toBe(false);
  });
  it("labels", () => {
    expect(vidLabel(null)).toBe("untagged");
    expect(vidLabel(7)).toBe("vid 7");
  });
});
