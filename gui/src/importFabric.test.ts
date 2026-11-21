import { describe, expect, it } from "vitest";
import type { ImportSwitch } from "./api";
import { changed, emptyDraft, offeredKeys, parseAddresses, toChoices } from "./importFabric";

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
});
