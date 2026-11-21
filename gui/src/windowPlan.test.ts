import { describe, expect, it } from "vitest";
import { clampPick, defaultPick, insideUsual, pickOf, spanOf, until, usualDays } from "./windowPlan";

// Wednesday 28 Oct 2026 15:40 in the browser's zone (tests run in whatever zone the machine has:
// every expectation is built with local Date constructors).
const NOW = new Date(2026, 9, 28, 15, 40);
const local = (day: number, h: number, m = 0) => new Date(2026, 9, 28 + day, h, m);
const SAT = { start: local(3, 2).toISOString(), end: local(3, 5).toISOString() };

describe("change window picker", () => {
  it("presets the next usual opening", () => {
    expect(defaultPick([SAT], NOW)).toEqual({ day: 3, start: 120, end: 300 });
    expect(defaultPick([], NOW)).toEqual({ day: 1, start: 120, end: 300 });
  });

  it("marks the days a usual window opens", () => {
    expect([...usualDays([SAT], NOW)]).toEqual([3]);
    expect(pickOf({ start: local(9, 2).toISOString(), end: local(9, 5).toISOString() }, NOW)).toBeNull();   // too far
  });

  it("knows a window inside the usual one", () => {
    expect(insideUsual(spanOf({ day: 3, start: 150, end: 240 }, NOW), [SAT])).toBe(true);
    expect(insideUsual(spanOf({ day: 3, start: 150, end: 330 }, NOW), [SAT])).toBe(false);
    expect(insideUsual(spanOf({ day: 2, start: 120, end: 300 }, NOW), [SAT])).toBe(false);
  });

  it("never starts in the past and keeps a quarter hour at least", () => {
    expect(clampPick({ day: 0, start: 60, end: 120 }, NOW)).toEqual({ day: 0, start: 945, end: 1005 });
    expect(clampPick({ day: 1, start: 1430, end: 1440 }, NOW)).toEqual({ day: 1, start: 1425, end: 1440 });
  });

  it("says how far away a window is", () => {
    expect(until(local(0, 15, 52), NOW)).toBe("in 12 min");
    expect(until(local(0, 20, 40), NOW)).toBe("in 5 h");
    expect(until(local(3, 2), NOW)).toBe("in 2 days");
  });
});
