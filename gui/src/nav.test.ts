import { describe, expect, it } from "vitest";
import { href, parse, redirect, section } from "./nav";

describe("routes", () => {
  it("parses every page", () => {
    expect(parse("#/network/routing/leaf1+leaf2")).toEqual({ page: "routing-target", target: "leaf1+leaf2" });
    expect(parse("#/switches/leaf1")).toEqual({ page: "switch", name: "leaf1", tab: "ports" });
    expect(parse("#/overview/noc")).toEqual({ page: "noc", wall: false });
    expect(parse("#/overview/noc/wall")).toEqual({ page: "noc", wall: true });
    expect(parse("#/switches/leaf1/routing")).toEqual({ page: "switch", name: "leaf1", tab: "routing" });
    expect(parse("#/switches/leaf1/control")).toEqual({ page: "switch", name: "leaf1", tab: "control" });
    expect(parse("#/overview/health")).toEqual({ page: "health" });
    expect(parse("#/overview/timeline")).toEqual({ page: "timeline" });
    expect(parse("#/overview/capacity")).toEqual({ page: "capacity" });
    expect(parse("#/network/trace")).toEqual({ page: "trace" });
    expect(parse("#/network/ports")).toEqual({ page: "ports" });
    expect(parse("#/network/security")).toEqual({ page: "security" });
    expect(parse("#/overview/flows")).toEqual({ page: "flows" });
    expect(parse("#/switches/leaf1/flows")).toEqual({ page: "switch", name: "leaf1", tab: "flows" });
    expect(parse("#/switches/leaf1/hardware")).toEqual({ page: "switch", name: "leaf1", tab: "hardware" });
    expect(parse("#/switches/leaf1/nonsense")).toEqual({ page: "switch", name: "leaf1", tab: "ports" });
    expect(parse("#/switches/inventory")).toEqual({ page: "inventory" });
    expect(parse("#/switches/software")).toEqual({ page: "software" });
    expect(parse("#/changes/upgrades/7")).toEqual({ page: "upgrade", id: 7 });
    expect(parse("#/changes/deploys/12")).toEqual({ page: "deploy", id: 12 });
    expect(parse("#/changes/checks/7")).toEqual({ page: "check", id: 7 });
    expect(parse("#/fabric/import")).toEqual({ page: "import-fabric" });
    expect(parse("")).toEqual({ page: "overview" });
    expect(parse("#/whatever")).toEqual({ page: "overview" });
  });

  it("sends every address of an older version to its new place", () => {
    const old: Record<string, string> = {
      "#/topology": "#/overview",
      "#/segments": "#/network",
      "#/routing": "#/network/routing",
      "#/routing/leaf1+leaf2": "#/network/routing/leaf1%2Bleaf2",
      "#/devices": "#/switches",
      "#/devices/leaf1": "#/switches/leaf1",
      "#/add-switches": "#/switches/add",
      "#/inventory": "#/switches/inventory",
      "#/routes": "#/switches",
      "#/routes/leaf1": "#/switches/leaf1/routing",
      "#/drift": "#/changes",
      "#/deploys": "#/changes/history",
      "#/deploys/12": "#/changes/deploys/12",
      "#/diffs": "#/changes/history",
      "#/diffs/7": "#/changes/checks/7",
      "#/audit": "#/changes/audit",
      "#/new-fabric": "#/fabric/new",
      "#/import-fabric": "#/fabric/import",
    };
    for (const [from, to] of Object.entries(old)) expect(redirect(from), from).toBe(to);
    expect(parse(redirect("#/routing/leaf1+leaf2")!)).toEqual({ page: "routing-target", target: "leaf1+leaf2" });
    for (const current of ["#/overview", "#/network", "#/switches/leaf1", "#/changes/history", "#/fabric", ""]) {
      expect(redirect(current), current).toBeNull();
    }
  });

  it("puts each page under its menu entry", () => {
    expect(section(parse("#/switches/leaf1/config"))).toBe("switches");
    expect(section(parse("#/changes/deploys/3"))).toBe("changes");
    expect(section(parse("#/network/routing"))).toBe("network");
    expect(section(parse("#/fabric/new"))).toBe("fabric");
  });

  it("builds links that parse back", () => {
    expect(parse(href.switchPage("leaf1", "settings"))).toEqual({ page: "switch", name: "leaf1", tab: "settings" });
    expect(href.switchPage("leaf1")).toBe("#/switches/leaf1");
    expect(parse(href.routingTarget("a+b"))).toEqual({ page: "routing-target", target: "a+b" });
  });
});

describe("the Admin entry", () => {
  it("leads to the accounts page", () => {
    expect(parse("#/admin/users")).toEqual({ page: "users" });
    expect(section(parse("#/admin/users"))).toBe("admin");
    expect(parse("#/admin/settings")).toEqual({ page: "settings" });
  });
});

