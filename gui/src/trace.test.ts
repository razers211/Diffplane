import { describe, expect, it } from "vitest";
import type { TraceResult } from "./api";
import { RESULT } from "./PathTrace";

describe("path trace results", () => {
  it("names every result the orchestrator gives", () => {
    const results: TraceResult[] = ["delivered", "gateway", "firewall", "isp", "drop", "unknown"];
    for (const r of results) expect(RESULT[r].text.length).toBeGreaterThan(0);
    expect(RESULT.drop.pill).toBe("pill bad");
  });
});
