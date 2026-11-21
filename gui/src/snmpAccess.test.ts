import { describe, expect, it } from "vitest";
import { passwordProblem, removeBlocked, switchLines, v2cOffBlocked } from "./snmpAccess";

const nms = { auth: "sha2-256", priv: "aes256" };

describe("monitoring access", () => {
  it("writes the generator's version lines and readers", () => {
    expect(switchLines({ v2c: true, readers: {}, traps: {} })).toEqual(
      ["snmp-agent sys-info version v2c", "snmp-agent sys-info version v3 disable", "snmp-agent community read ****** alias ro-…"]);
    const lines = switchLines({ v2c: false, readers: { nms }, traps: { a: { address: "192.0.2.10", reader: "nms" }, b: { address: "192.0.2.11", reader: "gone" } } });
    expect(lines.slice(0, 4)).toEqual(["snmp-agent sys-info version v3", "snmp-agent group v3 FABRIC-RO privacy notify-view ViewDefault",
      "snmp-agent usm-user v3 nms", "snmp-agent usm-user v3 nms group FABRIC-RO"]);
    expect(lines.slice(-2)).toEqual(["snmp-agent protocol source-interface MEth0/0/0", "snmp-agent protocol vpn-instance _management_vpn_"]);
    expect(lines.filter((l) => l.includes("target-host"))).toHaveLength(1);   // a trap's reader must exist
    expect(switchLines({ v2c: true, readers: { nms }, traps: {} })[0]).toBe("snmp-agent sys-info version v2c v3");
  });

  it("says why v2c or a reader cannot go", () => {
    expect(v2cOffBlocked({ v2c: true, readers: {}, traps: {} })).toContain("v3 reader");
    expect(v2cOffBlocked({ v2c: true, readers: { nms }, traps: {} })).toBeNull();
    expect(removeBlocked({ v2c: true, readers: { nms }, traps: { t: { address: "192.0.2.1", reader: "nms" } } }, "nms")).toContain("t");
    expect(removeBlocked({ v2c: false, readers: { nms }, traps: {} }, "nms")).toContain("v2c on");
    expect(removeBlocked({ v2c: true, readers: { nms }, traps: {} }, "nms")).toBeNull();
  });

  it("checks passwords as the API does", () => {
    expect(passwordProblem("Fabric-2026x")).toBeNull();
    for (const bad of ["Short1!", "has space 1A", "what?1Aa", "alllowercase"]) expect(passwordProblem(bad)).not.toBeNull();
  });
});
