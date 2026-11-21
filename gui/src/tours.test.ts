import { describe, expect, it } from "vitest";
import { GLOSSARY } from "./glossary";
import { TOURS, visibleSteps } from "./tours";

describe("walk-throughs", () => {
  it("covers every page, with short steps", () => {
    const pages = ["overview", "health", "timeline", "capacity", "flows", "noc", "network", "network-routing", "routing-target", "trace", "ports", "security", "qos", "protection", "switches", "inventory", "add-switches", "switch", "software", "upgrade",
      "changes", "history", "audit", "deploy", "check", "fabric", "new-fabric", "import-fabric", "dci", "users", "welcome", "settings", "directory"];
    expect(Object.keys(TOURS).sort()).toEqual([...pages].sort());
    for (const [page, steps] of Object.entries(TOURS)) {
      expect(steps!.length, page).toBeGreaterThan(0);
      for (const s of steps!) expect(s.text.length, `${page}: ${s.selector}`).toBeLessThan(220);
    }
  });

  it("skips a part that is not on screen", () => {
    const steps = [{ selector: ".a", text: "A" }, { selector: ".b", text: "B" }];
    expect(visibleSteps(steps, (s) => s === ".b").map((s) => s.text)).toEqual(["B"]);
  });

  it("explains every word in plain, short sentences", () => {
    for (const [word, text] of Object.entries(GLOSSARY)) expect(text.length, word).toBeLessThan(220);
  });
});
