import { describe, expect, it, vi } from "vitest";
import { DRAFT_SAVED, draftHeaders, setDraft, stepText } from "./draft";

const step = (method: string, path: string, body: object | null = null) =>
  ({ method, path, body: body && JSON.stringify(body), user: "op", at: "2026-10-28T10:00:00Z" });

describe("draft mode", () => {
  it("sends the draft's number with every request while it is on", () => {
    vi.stubGlobal("window", new EventTarget());   // no sessionStorage here: the in-memory fallback is used
    setDraft({ site: "example", id: 4 });
    expect(draftHeaders()).toEqual({ "X-Change-Set": "4" });
    setDraft(null);
    expect(draftHeaders()).toEqual({});
  });

  it("says each saved change in plain words", () => {
    expect(stepText(step("POST", "/segments", { name: "web" }))).toBe("Segment created: web");
    expect(stepText(step("POST", "/segments/example/10500/attachments", { device: "leaf1", interface: "10GE1/0/3" })))
      .toBe("Port attached: leaf1 10GE1/0/3");
    expect(stepText(step("PUT", "/routing/example/devices/leaf1"))).toBe("Routing changed");
    expect(stepText(step("PUT", "/somewhere/else"))).toBe("PUT /somewhere/else");
  });
});

describe("202 answers", () => {
  const answer = (body: object) => vi.fn(async () => new Response(JSON.stringify(body), { status: 202 }));

  it("unwraps a draft save and keeps a job's own answer", async () => {
    vi.stubGlobal("window", new EventTarget());
    const { requestDeploy, setPortSettingsManaged } = await import("./api");
    const saved = vi.fn();
    window.addEventListener(DRAFT_SAVED, saved);
    vi.stubGlobal("fetch", answer({ id: 1379 }));
    expect(await requestDeploy({ site: "example" })).toEqual({ id: 1379 });
    expect(saved).not.toHaveBeenCalled();
    vi.stubGlobal("fetch", answer({ change_set: 4, step: 1, result: { enabled: true } }));
    expect(await setPortSettingsManaged("example", true)).toEqual({ enabled: true });
    expect(saved).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
