import { describe, expect, it } from "vitest";
import type { ImportPlan, ImportSwitch, OverrideEntry } from "./api";
import { changed, emptyDraft, importReport, offeredKeys, parseAddresses, toChoices } from "./importFabric";

const key = (k: string) => ({ key: k, type: "ssh-rsa", sha256: "SHA256:x", md5: "00" });

describe("import fabric", () => {
  it("reads addresses one per line or comma separated, once each", () => {
    expect(parseAddresses("10.0.0.1/24\n10.0.0.2/24, 10.0.0.1/24 ")).toEqual(["10.0.0.1/24", "10.0.0.2/24"]);
  });
  it("approves exactly the keys offered on both ports", () => {
    const s = { offered: { "22": [key("A")], "830": [key("A"), key("B")] } } as unknown as ImportSwitch;
    expect(offeredKeys(s)).toEqual(["A", "B"]);
  });
  it("sends only what the operator changed", () => {
    const d = { ...emptyDraft(), name: " dc9 ", pools: { gateways: " 10.100.0.0/16 ", p2p: "" }, architecture: "centralized" as const, gatewayPair: "a+b" };
    expect(toChoices(d)).toEqual({ name: "dc9", pools: { gateways: "10.100.0.0/16" }, gateway_mac: null, roles: {},
      architecture: "centralized", gateway_pair: ["a", "b"] });
    expect(toChoices(emptyDraft()).architecture).toBeNull();
  });
  it("lists the switches the platform would change", () => {
    expect(changed({ b: "x\n", a: "", c: " \n" })).toEqual(["b"]);
  });
  it("groups the report per switch read, busy switches first", () => {
    const entry = (scope: string, target: string | null) => ({ scope, target, path: `${scope}/${target}` }) as OverrideEntry;
    const plan = {
      devices: [{ name: "a", planned: false }, { name: "b", planned: false }, { name: "c", planned: true }],
      misfits: { b: ["VLAN-based port 10GE1/0/1"], c: [], "segment 10": ["no gateway MAC"] },
    } as unknown as ImportPlan;
    const kept = [entry("switch", "a"), entry("switch", "c"), entry("fabric", null)];
    const report = importReport(plan, kept, { a: "", b: "+ interface X" });
    expect(report.switches.map((s) => s.name)).toEqual(["b", "a"]);
    expect(report.switches[1].kept).toEqual([kept[0]]);
    expect(report.switches[0].misfits).toEqual(["VLAN-based port 10GE1/0/1"]);
    expect(report.kept).toEqual([kept[1], kept[2]]);   // a planned switch's values go under the fabric
    expect(report.misfits).toEqual([["segment 10", ["no gateway MAC"]]]);
  });
});
