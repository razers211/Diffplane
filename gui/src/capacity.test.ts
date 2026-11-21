import { describe, expect, it } from "vitest";
import type { Forecast } from "./api";
import { forecastText } from "./Capacity";

const base: Forecast = { status: "rising", days: 40, earliest: 31, latest: 58, days_of_data: 28,
  threshold_bps: 7e9, slope_bps_per_day: 1e8, today_bps: 3e9 };

describe("capacity forecast in words", () => {
  it("says when 70 % is reached, with its range", () => {
    expect(forecastText(base, 0.7)).toBe("70 % in about 40 days (31–58)");
    expect(forecastText({ ...base, latest: null }, 0.7)).toBe("70 % in about 40 days (31–never)");
    expect(forecastText({ ...base, days: 1, earliest: 1, latest: 1 }, 0.7)).toBe("70 % in about 1 day");
  });

  it("says why there is no date", () => {
    expect(forecastText({ ...base, status: "over threshold" }, 0.7)).toBe("over 70 % now");
    expect(forecastText({ ...base, status: "not rising" }, 0.7)).toBe("not rising");
    expect(forecastText({ ...base, status: "not enough data", days_of_data: 3 }, 0.7)).toBe("needs 7 days of data (has 3)");
    expect(forecastText({ ...base, status: "no speed" }, 0.7)).toBe("speed not known");
  });
});
