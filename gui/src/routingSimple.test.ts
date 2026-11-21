import { describe, expect, it } from "vitest";
import type { BgpPeer, DeviceRouting } from "./routingIntent";
import { addNetwork, applyChoice, autoName, choiceBlocker, choiceOf, connect, disconnect, gateRules, networkWords, setGate, setSteps, usedBy } from "./routingSimple";

const isp: BgpPeer = { vpn: "ISP-A", address: "203.0.113.9", asn: 64510 };
const example: BgpPeer = { address: "10.255.9.2", asn: 65300 };

describe("simple routing view", () => {
  it("names the platform's policies after the VPN, or the peer when the VPN has several", () => {
    expect(autoName({ bgp_peers: [isp] }, isp, "imp")).toBe("ISP-A-IN");
    expect(autoName({ bgp_peers: [isp, { ...isp, address: "203.0.113.10" }] }, isp, "exp")).toBe("PEER-203-0-113-9-OUT");
    expect(autoName({ bgp_peers: [example] }, example, "exp")).toBe("PEER-10-255-9-2-OUT");
    expect(autoName({}, { address: "fd00:99::2", asn: 1 }, "imp")).toBe("PEER-fd00-99-2-IN");
  });

  it("connects to an internet provider accepting only the default route, and reads it back", () => {
    const r = connect({}, isp, true) as DeviceRouting;
    expect(r.prefix_lists).toEqual({ DEFAULT: [{ index: 10, action: "permit", prefix: "0.0.0.0/0" }] });
    expect(r.route_policies).toEqual({ "ISP-A-IN": [
      { node: 10, action: "permit", match_ip_prefix: "DEFAULT", apply_local_preference: null }, { node: 20, action: "deny" }] });
    expect(r.bgp_peers).toEqual([{ ...isp, import_policy: "ISP-A-IN", import_prefix_list: null }]);
    expect(choiceOf(r, r.bgp_peers![0], "imp", true)).toEqual({ id: "default", lp: null });
    expect(choiceOf(r, r.bgp_peers![0], "exp", true)).toEqual({ id: "unset" });
    expect(connect(r, isp, true)).toBe("This router is already connected.");
  });

  it("writes and reads every plain choice, preference included", () => {
    let r: DeviceRouting = { bgp_peers: [example], prefix_lists: { PARTNER: [{ index: 10, action: "permit", prefix: "10.30.0.0/16" }] },
                             route_policies: { MINE: [{ node: 10, action: "permit" }] } };
    r = applyChoice(r, r.bgp_peers![0], "imp", { id: "list", list: "PARTNER", lp: 200 });
    expect(choiceOf(r, r.bgp_peers![0], "imp", false)).toEqual({ id: "list", list: "PARTNER", lp: 200 });
    r = applyChoice(r, r.bgp_peers![0], "imp", { id: "none" });
    expect(choiceOf(r, r.bgp_peers![0], "imp", false)).toEqual({ id: "none" });
    r = applyChoice(r, r.bgp_peers![0], "imp", { id: "set", set: "MINE" });
    expect(choiceOf(r, r.bgp_peers![0], "imp", false)).toEqual({ id: "set", set: "MINE" });
    expect(Object.keys(r.route_policies!)).toEqual(["MINE"]);   // the platform's policy nobody uses is gone, the engineers' stays
    r = applyChoice(r, r.bgp_peers![0], "imp", { id: "all" });
    expect(r.bgp_peers![0].import_policy).toBeNull();
    expect(choiceOf(r, r.bgp_peers![0], "imp", false)).toEqual({ id: "all" });
  });

  it("leaves what it cannot say plainly to the expert view", () => {
    expect(choiceOf({}, { ...example, export_prefix_list: "X" }, "exp", false)).toEqual({ id: "expert" });
    const custom = { route_policies: { "X-IN": [{ node: 10, action: "permit" as const, match_ip_prefix: "A" }] } };
    expect(choiceOf(custom, { ...example, import_policy: "X-IN" }, "imp", false)).toEqual({ id: "set", set: "X-IN" });
  });

  it("refuses sending everything to an internet provider and choices with nothing to use", () => {
    expect(choiceBlocker({}, "exp", { id: "all" }, true)).toBe("Not allowed towards an internet provider.");
    expect(choiceBlocker({}, "exp", { id: "own" }, true)).toBe("Make the network list OWN-PREFIXES first (below).");
    expect(choiceBlocker({}, "exp", { id: "all" }, false)).toBeNull();
  });

  it("adds networks to a list, one family per list", () => {
    let r = addNetwork({}, "PARTNER", "10.20.0.0/16", true) as DeviceRouting;
    r = addNetwork(r, "PARTNER", "10.21.0.1/32", true) as DeviceRouting;
    expect(r.prefix_lists!.PARTNER).toEqual([
      { index: 10, action: "permit", prefix: "10.20.0.0/16", le: 32 }, { index: 20, action: "permit", prefix: "10.21.0.1/32", le: null }]);
    expect(networkWords(r.prefix_lists!.PARTNER[0])).toBe("10.20.0.0/16 and smaller");
    expect(addNetwork(r, "PARTNER", "fd00::/48", false)).toBe("A list holds IPv4 or IPv6 networks, not both.");
    expect(addNetwork(r, "PARTNER", "10.0.0.0", false)).toBe("Write the network like 10.20.0.0/16.");
  });

  it("says what uses a list, and renumbers a rule set's steps", () => {
    const r = applyChoice({ bgp_peers: [example], prefix_lists: { P: [] } }, example, "imp", { id: "list", list: "P" });
    expect(usedBy(r, "P", (p) => p.address)).toEqual(["a connection's choice (PEER-10-255-9-2-IN)"]);
    expect(usedBy(r, "PEER-10-255-9-2-IN", (p) => p.address)).toEqual(["accepting from 10.255.9.2"]);
    expect(setSteps({}, "S", [{ node: 30, action: "deny" }, { node: 5, action: "permit" }]).route_policies!.S.map((s) => s.node)).toEqual([10, 20]);
    expect(disconnect(r, r.bgp_peers![0]).route_policies).toEqual({});
  });
});

describe("route flow gates (expert view)", () => {
  const r: DeviceRouting = { prefix_lists: { P: [{ index: 10, action: "permit", prefix: "10.30.0.0/16", le: 32 }] },
    route_policies: { POL: [{ node: 10, action: "permit", match_ip_prefix: "P", apply_local_preference: 200 }, { node: 20, action: "deny" }] } };
  it("says what each filter lets through, the implicit deny included", () => {
    expect(gateRules(r, example, "imp")).toEqual([{ ok: true, text: "every route" }]);
    expect(gateRules(r, { ...example, import_policy: "POL" }, "imp")).toEqual([
      { ok: true, text: "10.30.0.0/16 ≤/32", lp: 200 }, { ok: false, text: "the rest", lp: undefined }]);
    expect(gateRules(r, { ...example, export_prefix_list: "P" }, "exp")).toEqual([{ ok: true, text: "only 10.30.0.0/16 ≤/32" }, { ok: false, text: "the rest" }]);
    expect(gateRules({ ...r, route_policies: { M: [{ node: 10, action: "permit", match_ip_prefix: "P" }] } }, { ...example, import_policy: "M" }, "imp").at(-1))
      .toEqual({ ok: false, text: "the rest" });
  });
  it("replaces a direction's filter with the dropped one", () => {
    const withList = setGate(r, { ...example, import_policy: "POL" }, "imp", "plist", "P");
    expect(withList.bgp_peers![0]).toMatchObject({ import_policy: null, import_prefix_list: "P" });
    expect(setGate(withList, withList.bgp_peers![0], "imp", null, null).bgp_peers![0]).toMatchObject({ import_policy: null, import_prefix_list: null });
  });
});
