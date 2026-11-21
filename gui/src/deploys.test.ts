import { describe, expect, it } from "vitest";
import { actionTone } from "./Deploys";

describe("actionTone", () => {
  it("colours actions by the kind of change", () => {
    expect(actionTone("deploy.approve")).toBe("accent");
    expect(actionTone("service.delete")).toBe("bad");
    expect(actionTone("segment.create")).toBe("good");
    expect(actionTone("auth.login")).toBe("");
    expect(actionTone("routing.set")).toBe("warn");
    expect(actionTone("deploy.failed")).toBe("bad");
    expect(actionTone("deploy.verified")).toBe("good");
    expect(actionTone("login.failed")).toBe("warn");
  });
});
