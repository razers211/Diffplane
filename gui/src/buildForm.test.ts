import { describe, expect, it } from "vitest";
import type { BuildOptions } from "./api";
import {
  autoLinks, batchProblem, cableOn, canBeGateway, emptySwitch, inventoryChoices, leafChoices, peerLinkChoices, GENERIC, linksForDetected, placeDetected, portChoices, spineChoices, toBatch, unpairedSpines, withDetected, type LinkDraft, type SwitchDraft, nextNames, staggered } from "./buildForm";

const MODEL = "CE6881H-48S6CQ";
const opts: BuildOptions = {
  fabric: "example",
  models: { [MODEL]: ["100GE1/0/1", "100GE1/0/2", "100GE1/0/5", "100GE1/0/6"] },
  devices: [
    { name: "spine1", role: "spine", free_ports: ["100GE1/0/3", "100GE1/0/4", "100GE1/0/6"] },
    { name: "leaf1", role: "leaf", free_ports: ["100GE1/0/2"] },
  ],
  bypass: true,
};
const pair: SwitchDraft = { ...emptySwitch("pair", MODEL), names: ["leaf3", "leaf4"], peerLinkMembers: ["100GE1/0/5", "100GE1/0/6"] };
const lone: SwitchDraft = { ...emptySwitch("leaf", MODEL), names: ["leaf5"], role: "border-leaf", management: ["172.24.0.125/24"] };
const spine: SwitchDraft = { ...emptySwitch("spine", MODEL), names: ["spine2"] };
const link = (spine: string, sp: string, leaf: string, lp: string): LinkDraft => ({ spine, spine_port: sp, leaf, leaf_port: lp });
const links = [
  link("spine1", "100GE1/0/3", "leaf3", "100GE1/0/1"),
  link("spine1", "100GE1/0/4", "leaf4", "100GE1/0/1"),
  link("spine2", "100GE1/0/1", "leaf5", "100GE1/0/1"),
];

describe("add several switches", () => {
  it("accepts pairs, lone leaves and spines with their links", () => {
    expect(batchProblem(opts, [pair, lone, spine], links)).toBeNull();
  });

  it("offers new and existing switches on both ends of a link", () => {
    expect(spineChoices(opts, [pair, spine])).toEqual(["spine1", "spine2"]);
    expect(leafChoices(opts, [pair, lone])).toEqual(["leaf1", "leaf3", "leaf4", "leaf5"]);
  });

  it("offers each port once, never a new pair's peer-link members", () => {
    expect(portChoices(opts, [pair], links, "leaf3", 1)).toEqual(["100GE1/0/2"]);   // .1 used in row 0
    expect(portChoices(opts, [pair], links, "leaf3", 0)).toEqual(["100GE1/0/1", "100GE1/0/2"]);
    expect(portChoices(opts, [pair], links, "spine1", 2)).toEqual(["100GE1/0/6"]);   // existing: free ports
  });

  it("explains what is wrong", () => {
    expect(batchProblem(opts, [], [])).toMatch(/at least one switch/);
    expect(batchProblem(opts, [pair, { ...lone, names: ["leaf3"] }], links)).toMatch(/appears twice/);
    expect(batchProblem(opts, [{ ...spine, names: ["spine1"] }], [])).toMatch(/already exists/);
    expect(batchProblem(opts, [pair, lone, spine], links.slice(0, 2))).toMatch(/leaf5 needs at least one uplink/);
    expect(batchProblem(opts, [pair, { ...lone, management: ["172.24.0.125"] }, spine], links)).toMatch(/with its mask/);
    expect(batchProblem(opts, [pair, lone, { ...spine, management: ["172.24.0.125/24"] }], links)).toMatch(/given twice/);
    expect(batchProblem(opts, [spine], [link("spine1", "100GE1/0/3", "leaf1", "100GE1/0/2")])).toMatch(/needs a new switch/);
    expect(batchProblem(opts, [{ ...pair, peerLinkMembers: [] }], links.slice(0, 2))).toMatch(/peer-link member/);
  });

  it("builds the request", () => {
    const body = toBatch([pair, lone, spine], links);
    expect(body.pairs[0]).toMatchObject({ leaves: ["leaf3", "leaf4"], peer_link_trunk: 0, management: {} });
    expect(body.leaves).toEqual([{ name: "leaf5", model: MODEL, role: "border-leaf", management: "172.24.0.125/24", inventory: null }]);
    expect(body.spines).toEqual([{ name: "spine2", model: MODEL, management: null, inventory: null }]);
    expect(body.links).toHaveLength(3);
  });
});

describe("spine pairs (centralized fabrics)", () => {
  const central: BuildOptions = { ...opts, centralized: true, gateway_pair: ["leaf1", "leaf2"] };
  const withSpare: BuildOptions = {
    ...central,
    devices: [{ ...central.devices[0], free_ports: ["100GE1/0/3", "100GE1/0/5", "100GE1/0/6"] }, central.devices[1]],
  };
  const sp: SwitchDraft = { ...emptySwitch("spinepair", MODEL), names: ["spine1", "spine2"], peerLinkMembers: ["100GE1/0/5", "100GE1/0/6"] };
  const up = [link("spine2", "100GE1/0/1", "leaf1", "100GE1/0/2")];

  it("pairs an existing single spine with a new one", () => {
    expect(unpairedSpines(withSpare)).toEqual(["spine1"]);
    expect(peerLinkChoices(withSpare, sp)).toEqual(["100GE1/0/5", "100GE1/0/6"]);   // model ports free on spine1
    expect(batchProblem(withSpare, [sp], up)).toBeNull();
    expect(spineChoices(withSpare, [sp])).toEqual(["spine1", "spine2"]);   // spine1 listed once
    expect(portChoices(withSpare, [sp], up, "spine1", 1)).toEqual(["100GE1/0/3"]);   // not its peer-link members
  });

  it("refuses what the orchestrator would refuse", () => {
    expect(batchProblem(opts, [sp], up)).toMatch(/centralized fabric/);
    expect(batchProblem(central, [sp], up)).toMatch(/100GE1\/0\/5 is not free/);
    expect(batchProblem(withSpare, [{ ...sp, names: ["spine1", "leaf1"] }], up)).toMatch(/leaf1 is not a single spine/);
    expect(batchProblem(withSpare, [{ ...sp, gateway: true }, { ...sp, names: ["spine3", "spine4"], gateway: true }], up))
      .toMatch(/Only one pair/);
  });

  it("sends the pair, without a management address for the existing spine", () => {
    const body = toBatch([{ ...sp, gateway: true, management: ["172.24.0.9/24", "172.24.0.127/24"] }], up, withSpare);
    expect(body.spine_pairs).toEqual([{
      spines: ["spine1", "spine2"], model: MODEL, peer_link_trunk: 0, peer_link_members: ["100GE1/0/5", "100GE1/0/6"],
      management: { "spine2": "172.24.0.127/24" }, inventory: {}, gateway: true,
    }]);
  });
});

describe("a new fabric and the inventory", () => {
  const fresh: BuildOptions = { fabric: "dc9", models: opts.models, devices: [], bypass: true, fresh: true, centralized: true,
    inventory: [{ serial: "SN1", name: "inv-SN1", model: MODEL, mac: null }, { serial: "SN2", name: "inv-SN2", model: "CE0", mac: null }] };
  const gw: SwitchDraft = { ...emptySwitch("pair", MODEL), names: ["dc9-gw1", "dc9-gw2"], role: "border-leaf",
    peerLinkMembers: ["100GE1/0/5", "100GE1/0/6"] };

  it("needs the gateway pair of a new centralized fabric", () => {
    expect(batchProblem(fresh, [gw], [])).toMatch(/needs its gateway pair/);
    expect(batchProblem(fresh, [{ ...gw, gateway: true }], [])).toBeNull();
    expect(canBeGateway(fresh, { ...gw, role: "leaf" })).toBe(false);
    expect(canBeGateway({ ...fresh, centralized: false }, gw)).toBe(false);
    expect(toBatch([{ ...gw, gateway: true }], [], fresh).pairs[0].gateway).toBe(true);
  });

  it("picks inventory switches of the same model, each once", () => {
    const picked = { ...gw, gateway: true, inventory: ["SN1", ""] };
    expect(inventoryChoices(fresh, [picked], picked, 1)).toEqual([]);   // SN1 taken by leaf 1; SN2 another model
    expect(inventoryChoices(fresh, [picked], picked, 0).map((s) => s.serial)).toEqual(["SN1"]);
    expect(batchProblem(fresh, [{ ...picked, inventory: ["SN1", "SN1"] }], [])).toMatch(/picked twice/);
    expect(batchProblem(fresh, [{ ...picked, inventory: ["SN2", ""] }], [])).toMatch(/is a CE0/);
    expect(toBatch([picked], [], fresh).pairs[0].inventory).toEqual({ "dc9-gw1": "SN1" });
  });
});

describe("proposed names", () => {
  it("takes the next free leaf or spine names, skipping the batch", () => {
    const opts = { fabric: "east", models: {}, bypass: true, devices: [
      { name: "east-leaf1", role: "leaf", free_ports: [] }, { name: "east-leaf2", role: "leaf", free_ports: [] },
      { name: "east-spine1", role: "spine", free_ports: [] }] };
    expect(nextNames(opts, [], "east", "pair")).toEqual(["east-leaf3", "east-leaf4"]);
    expect(nextNames(opts, [], "east", "spine")).toEqual(["east-spine2"]);
    const pair = { ...emptySwitch("pair", "m"), names: ["east-leaf3", "east-leaf4"] };
    expect(nextNames(opts, [pair], "east", "leaf")).toEqual(["east-leaf5"]);
  });
});


describe("cabling on the map", () => {
  it("cables a new pair to every spine on the fastest free ports, each port once", () => {
    const tenGig = { ...opts, models: { [MODEL]: ["10GE1/0/1", "100GE1/0/1", "100GE1/0/2", "100GE1/0/5", "100GE1/0/6"] } };
    const drafts = [{ ...pair, peerLinkMembers: ["100GE1/0/5", "100GE1/0/6"] }, spine];
    expect(autoLinks(tenGig, drafts, [], ["leaf3", "leaf4"], false)).toEqual([
      link("spine1", "100GE1/0/3", "leaf3", "100GE1/0/1"),
      link("spine2", "100GE1/0/1", "leaf3", "100GE1/0/2"),
      link("spine1", "100GE1/0/4", "leaf4", "100GE1/0/1"),
      link("spine2", "100GE1/0/2", "leaf4", "100GE1/0/2"),
    ]);
  });

  it("cables a new spine to every leaf, skipping a leaf with no free port", () => {
    const full = { ...opts, devices: [...opts.devices, { name: "leaf9", role: "leaf", free_ports: [] }] };
    expect(autoLinks(full, [spine], [], ["spine2"], true)).toEqual([link("spine2", "100GE1/0/1", "leaf1", "100GE1/0/2")]);
    expect(cableOn(full, [spine], [], "spine2", "leaf9")).toBeNull();
  });
});

describe("detected switches", () => {
  const session = {
    id: "s1",
    switches: [{
      address: "172.24.0.130/24", offered: {}, same_on_both_ports: true, approved: true, approved_by: "a", sysname: null,
      read: false, error: null, in_netbox: false,
      detected: { model: "CE6863H-48S6CQ", version: "V300", patch: "", serial: "X1", ports: { "25GE1/0/1": "25gbase-x-sfp28", "100GE1/0/1": "100gbase-x-qsfp28" } },
    }],
  };
  it("offer their model with the ports they reported", () => {
    expect(withDetected(opts, session).models["CE6863H-48S6CQ"]).toEqual(["25GE1/0/1", "100GE1/0/1"]);
    expect(withDetected(opts, null)).toBe(opts);
  });
  it("give a new switch their address and model, and leave no other switch with that address", () => {
    const placed = placeDetected([pair, { ...lone, management: ["172.24.0.130/24"] }], "leaf4", "172.24.0.130/24", "CE6863H-48S6CQ");
    expect(placed[0]).toMatchObject({ model: "CE6863H-48S6CQ", management: ["", "172.24.0.130/24"], peerLinkMembers: [] });
    expect(placed[1].management).toEqual([""]);
  });
  it("a generic switch: no cables and not sent until detected, then cabled with the ports it reported", () => {
    const generic: SwitchDraft = { ...emptySwitch("leaf", GENERIC), names: ["leaf9"] };
    const found = withDetected(opts, session);
    expect(autoLinks(found, [generic], [], ["leaf9"], false)).toEqual([]);
    expect(batchProblem(found, [generic], [])).toMatch(/leaf9: detect it/);
    const placed = placeDetected([generic], "leaf9", "172.24.0.130/24", "CE6863H-48S6CQ");
    const cabled = linksForDetected(found, [generic], placed, [], "leaf9");
    expect(cabled).toEqual([link("spine1", "100GE1/0/3", "leaf9", "100GE1/0/1")]);
    expect(batchProblem(found, placed, cabled)).toBeNull();
    expect(linksForDetected(found, placed, placed, cabled, "leaf9")).toBe(cabled);   // a model already: unchanged
  });
});

describe("staggered", () => {
  it("drops a label that would run into its neighbour's, row by row", () => {
    const at = (name: string, x: number, row = 1) => ({ name, x, row });
    // 13 characters are about 89 units wide: 80 apart they overlap, 120 apart they do not.
    expect(staggered([at("example-leaf1", 0), at("example-leaf2", 80), at("example-leaf3", 160)])).toEqual(new Set(["example-leaf2"]));
    expect(staggered([at("example-leaf1", 0), at("example-leaf2", 120)])).toEqual(new Set());
    expect(staggered([at("leaf1", 0), at("leaf2", 80)])).toEqual(new Set());
    expect(staggered([at("example-spine1", 0, 0), at("example-leaf1", 10, 1)])).toEqual(new Set());
    // What is measured is the label drawn: a shortened name fits.
    expect(staggered([{ ...at("example-leaf1", 0), label: "leaf1" }, { ...at("example-leaf2", 80), label: "leaf2" }])).toEqual(new Set());
  });
});
