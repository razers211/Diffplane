import { describe, expect, it } from "vitest";
import { allows } from "./role";

describe("role ladder", () => {
  it("lets higher roles do what lower ones can", () => {
    expect(allows("approver", "operator")).toBe(true);
    expect(allows("operator", "viewer")).toBe(true);
    expect(allows("operator", "approver")).toBe(false);
    expect(allows("viewer", "operator")).toBe(false);
  });
});
