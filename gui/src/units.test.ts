import { describe, expect, it } from "vitest";
import { axisBps, formatBps, formatCount, niceScale } from "./units";

describe("formatBps", () => {
  it("uses Mbps, Gbps or Tbps only", () => {
    expect(formatBps(0)).toBe("0.00 Mbps");
    expect(formatBps(12_345)).toBe("0.01 Mbps");
    expect(formatBps(950_000_000)).toBe("950.00 Mbps");
    expect(formatBps(2_500_000_000)).toBe("2.50 Gbps");
    expect(formatBps(1.2e12)).toBe("1.20 Tbps");
  });

  it("shows a dash when there is no data", () => {
    expect(formatBps(undefined)).toBe("—");
    expect(formatBps(null)).toBe("—");
    expect(formatBps(Number.NaN)).toBe("—");
  });
});

describe("formatCount", () => {
  it("rounds extrapolated increases", () => {
    expect(formatCount(2.9999)).toBe("3");
    expect(formatCount(0)).toBe("0");
    expect(formatCount(undefined)).toBe("—");
  });
});

describe("chart axes", () => {
  it("rounds the scale to 1, 2 or 5 times a power of ten", () => {
    expect(niceScale(7.4e9)).toEqual({ top: 8e9, step: 2e9 });
    expect(niceScale(950e6)).toEqual({ top: 1e9, step: 5e8 });
    expect(niceScale(0)).toEqual({ top: 1e6, step: 2.5e5 });
  });
  it("labels in Mbps, Gbps or Tbps from the scale", () => {
    expect(axisBps(2e9, 8e9)).toBe("2 Gbps");
    expect(axisBps(5e8, 1e9)).toBe("0.5 Gbps");
    expect(axisBps(5e8, 8e8)).toBe("500 Mbps");
  });
});
