import { describe, expect, it } from "vitest";
import type { TimelineEvent } from "./api";
import { eventTitle } from "./Timeline";

const base: TimelineEvent = { at: "2026-10-15T10:00:00Z", site: "example", device: "leaf1", kind: "link", subject: "10GE1/0/1",
  state: "down", previous: "up", severity: "problem", detail: null, user: null, job_id: null };

describe("timeline titles", () => {
  it("says what went from what to what", () => {
    expect(eventTitle(base)).toBe("10GE1/0/1: up → down");
    expect(eventTitle({ ...base, kind: "health", subject: "score", state: "warning", previous: "good" })).toBe("health good → warning");
    expect(eventTitle({ ...base, device: null, kind: "health", subject: "fabric score", state: "good", previous: "critical" }))
      .toBe("fabric health critical → good");
    expect(eventTitle({ ...base, kind: "alert", subject: "FabricPowerModuleFailed (module=PWR:1/1)", state: "resolved" }))
      .toBe("FabricPowerModuleFailed (module=PWR:1/1) resolved");
    expect(eventTitle({ ...base, kind: "change", subject: "deploy.verified", state: "" })).toBe("deploy verified");
  });
});
