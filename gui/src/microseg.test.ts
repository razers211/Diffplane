import { describe, expect, it } from "vitest";
import { addService, arrowPath, classifierOf, ringPoints, countersText, matrixEpgs, noContract, parseList, parseServices, servicesInput, servicesText, share, type Epg } from "./microseg";

const epg = (id: number, segment: number, name: string, group: string | null = null, active = true): Epg =>
  ({ id, segment, group, name, active, inside: "permit", prefixes: [], macs: [] });

describe("security groups", () => {
  it("reads services as people type them and shows them back", () => {
    const parsed = parseServices("tcp 5432, UDP 53-54, icmp, any, 47");
    expect(parsed).toEqual([{ protocol: "tcp", ports: [5432, 5432] }, { protocol: "udp", ports: [53, 54] },
      { protocol: "icmp" }, { protocol: "any" }, { protocol: 47 }]);
    if (typeof parsed === "string") throw new Error(parsed);
    expect(servicesText(parsed)).toBe("TCP 5432, UDP 53-54, ICMP, any, protocol 47");
    expect(parseServices(servicesInput(parsed))).toEqual(parsed);
  });

  it("says what is wrong with a service", () => {
    expect(parseServices("")).toMatch(/at least one/);
    expect(parseServices("icmp 8")).toMatch(/only for tcp or udp/);
    expect(parseServices("tcp 90-80")).toMatch(/lower first/);
    expect(parseServices("http")).toMatch(/any, tcp, udp/);
    expect(parseServices("tcp 1 2")).toMatch(/one port or a range/);
  });

  it("orders the matrix by segment, each followed by its groups, and leaves inactive EPGs out", () => {
    const epgs = [epg(3, 10500, "web-admins", "admins"), epg(2, 10600, "db"), epg(1, 10500, "web"), epg(4, 10700, null as never, null, false)];
    expect(matrixEpgs(epgs).map((e) => e.id)).toEqual([2, 1, 3]);
  });

  it("explains a cell without a contract in each mode", () => {
    const [a, b] = [epg(1, 10500, "web"), epg(2, 10600, "db")];
    expect(noContract("enforce", a, b)).toBe("denied");
    expect(noContract("learn", a, b)).toBe("allowed (learn)");
    expect(noContract("enforce", a, a)).toBe("allowed inside");
    expect(noContract("enforce", { ...a, inside: "deny" }, a)).toBe("denied inside");
  });

  it("splits typed lists and rounds shares up", () => {
    expect(parseList("10.0.0.16/28, 10.0.0.32/28\n0200-fc00-0001")).toEqual(["10.0.0.16/28", "10.0.0.32/28", "0200-fc00-0001"]);
    expect(share(1, 10240)).toBe(1);
    expect(share(0, 8192)).toBe(0);
  });

  it("names a contract as the switch does and shows its counters", () => {
    expect(classifierOf({ from: 1, to: 2 })).toBe("GBP-1-2");
    expect(countersText({ matched_packets: 1200, matched_bytes: 0, passed_packets: 0, passed_bytes: 0, dropped_packets: 3, dropped_bytes: 0 }))
      .toBe("1,200 matched · 3 dropped");
    expect(countersText(undefined)).toBe("not on this switch");
  });
});

describe("the group map", () => {
  it("puts the first group at the top, clockwise", () => {
    const [a, b] = ringPoints(4, 100, 100, 50);
    expect([Math.round(a.x), Math.round(a.y), Math.round(b.x), Math.round(b.y)]).toEqual([100, 50, 150, 100]);
  });

  it("bends an arrow to its left, so the reverse one does not overlap", () => {
    expect(arrowPath({ x: 0, y: 0 }, { x: 100, y: 0 }, 10)).toBe("M10.0 0.0 Q50.0 20.0 90.0 0.0");
    expect(arrowPath({ x: 100, y: 0 }, { x: 0, y: 0 }, 10)).toBe("M90.0 0.0 Q50.0 -20.0 10.0 0.0");
  });

  it("adds a quick pick to the services once, any replacing the rest", () => {
    expect(addService("", "tcp 443")).toBe("tcp 443");
    expect(addService("tcp 443,icmp", "tcp 443")).toBe("tcp 443, icmp");
    expect(addService("tcp 443", "udp 53")).toBe("tcp 443, udp 53");
    expect(addService("tcp 443", "any")).toBe("any");
    expect(addService("any", "tcp 22")).toBe("tcp 22");
  });
});
