import { describe, expect, it } from "vitest";
import { layout, refusal, trunkOrPort, type FacePort } from "./frontPanel";

const port = (name: string): FacePort => ({ name, kind: "free", detail: null, trunk: null, segments: [] });
const CE6881H = [...Array.from({ length: 48 }, (_, i) => port(`10GE1/0/${i + 1}`)), ...Array.from({ length: 6 }, (_, i) => port(`100GE1/0/${i + 1}`))];

describe("front panel layout", () => {
  it("puts odd ports on top, even below, slower ports first", () => {
    const { placed, width } = layout([...CE6881H].reverse());
    const at = (name: string) => placed.find((p) => p.port.name === name)!;
    expect(placed).toHaveLength(54);
    expect(at("10GE1/0/1").top).toBe(true);
    expect(at("10GE1/0/2").top).toBe(false);
    expect(at("10GE1/0/2").x).toBe(at("10GE1/0/1").x);
    expect(at("10GE1/0/3").x).toBeGreaterThan(at("10GE1/0/1").x);
    expect(at("100GE1/0/1").x).toBeGreaterThan(at("10GE1/0/48").x);
    expect(at("100GE1/0/1").w).toBeGreaterThan(at("10GE1/0/1").w);
    expect(width).toBeGreaterThan(at("100GE1/0/6").x + at("100GE1/0/6").w);
  });

  it("never overlaps two ports", () => {
    const { placed } = layout(CE6881H);
    for (const a of placed) for (const b of placed) {
      if (a === b) continue;
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      expect(apart, `${a.port.name} / ${b.port.name}`).toBe(true);
    }
  });
});


describe("refusals", () => {
  const p = (name: string, kind: FacePort["kind"], trunk: string | null = null, detail: string | null = null): FacePort =>
    ({ name, kind, trunk, detail, segments: [] });
  it("lets a trunk member pick its trunk and explains every refusal", () => {
    const allowed = new Set(["10GE1/0/12", "Eth-Trunk1"]);
    expect(trunkOrPort(p("10GE1/0/1", "trunk", "Eth-Trunk1"))).toBe("Eth-Trunk1");
    expect(refusal(p("10GE1/0/1", "trunk", "Eth-Trunk1"), allowed)).toBeNull();
    expect(refusal(p("10GE1/0/12", "free"), allowed)).toBeNull();
    expect(refusal(p("100GE1/0/1", "fabric", null, "to spine1 100GE1/0/1"), allowed)).toMatch(/link to another switch \(to spine1/);
    expect(refusal(p("100GE1/0/5", "peer", "Eth-Trunk0"), allowed)).toMatch(/peer-link/);
    expect(refusal(p("10GE1/0/13", "free"), allowed, undefined, "not free on leaf2")).toBe("not free on leaf2");
  });
});
