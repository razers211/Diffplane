// The simple routing view (prototype version 6): a switch's routing in plain
// words for new recruits. BGP peers are "connections", prefix lists "network lists", route-policies
// "rule sets", local-preference "preference", static routes "fixed routes". A plain choice for what
// we accept or send is written as a route-policy the platform names `<VPN or peer>-IN/-OUT`; any
// other policy is the engineers' own and shown as a rule set. Pure, so it is unit tested; the
// orchestrator validates every save as before.

import { putItem, putRule, removeRule, type BgpPeer, type DeviceRouting, type PolicyNode, type PrefixRule } from "./routingIntent";

export type Dir = "imp" | "exp";
export type ChoiceId = "all" | "none" | "unset" | "default" | "own" | "list" | "set" | "expert";
export interface Choice { id: ChoiceId; list?: string; set?: string; lp?: number | null }

export const PREF: [number, string][] = [[50, "Less"], [100, "Normal"], [200, "More"], [300, "Most"]];
export const prefWord = (lp: number | null | undefined) => PREF.find(([v]) => v === (lp ?? 100))?.[1] ?? `local-preference ${lp}`;
export const OWN = "OWN-PREFIXES";

const v6 = (peer: BgpPeer) => peer.address.includes(":");
const defaultList = (peer: BgpPeer) => (v6(peer) ? "DEFAULT-V6" : "DEFAULT");
const policyKey = (dir: Dir) => (dir === "imp" ? "import_policy" : "export_policy");
const listKey = (dir: Dir) => (dir === "imp" ? "import_prefix_list" : "export_prefix_list");
export const isAuto = (name: string) => /-(IN|OUT)$/.test(name);

/** The policy name the platform writes for a plain choice: the peer's VPN instance when it is the
 *  only peer there, else the peer's address. */
export function autoName(routing: DeviceRouting, peer: BgpPeer, dir: Dir): string {
  const alone = !!peer.vpn && (routing.bgp_peers ?? []).filter((p) => p.vpn === peer.vpn).length === 1;
  return `${alone ? peer.vpn : `PEER-${peer.address.replace(/[.:]+/g, "-")}`}-${dir === "imp" ? "IN" : "OUT"}`;
}

/** What a peer's filter does today, as one of the plain choices. */
export function choiceOf(routing: DeviceRouting, peer: BgpPeer, dir: Dir, isp: boolean): Choice {
  if (peer[listKey(dir)]) return { id: "expert" };   // a prefix-list filter on the peer: expert view
  const name = peer[policyKey(dir)];
  if (!name) return { id: dir === "exp" && isp ? "unset" : "all" };
  const nodes = [...(routing.route_policies?.[name] ?? [])].sort((a, b) => a.node - b.node);
  if (isAuto(name) && nodes.length === 1 && nodes[0].action === "deny" && !nodes[0].match_ip_prefix) return { id: "none" };
  if (isAuto(name) && nodes.length === 2 && nodes[0].action === "permit" && nodes[0].match_ip_prefix
      && nodes[1].action === "deny" && !nodes[1].match_ip_prefix) {
    const [m, lp] = [nodes[0].match_ip_prefix, nodes[0].apply_local_preference ?? null];
    if (m === defaultList(peer)) return { id: "default", lp };
    if (m === OWN && dir === "exp") return { id: "own" };
    return { id: "list", list: m, lp };
  }
  return { id: "set", set: name };
}

export function choiceText(c: Choice, dir: Dir): string {
  switch (c.id) {
    case "all": return dir === "imp" ? "Everything they send" : "Everything we know";
    case "none": return "Nothing";
    case "unset": return "Not chosen yet: today everything we know goes out";
    case "default": return "Only the default route";
    case "own": return "Our public prefixes";
    case "list": return `Only the networks in ${c.list}`;
    case "set": return `Rule set ${c.set}`;
    case "expert": return "A filter set in the expert view";
  }
}

export const CHOICES: Record<Dir, [ChoiceId, string, string][]> = {
  imp: [["default", "Only the default route", "Enough to reach the internet."], ["list", "Only the networks in a list", ""],
        ["all", "Everything they send", ""], ["set", "Follow a rule set", "Step-by-step rules you write below."],
        ["none", "Nothing", "Keep the connection, use none of its routes."]],
  exp: [["own", "Our public prefixes", `The network list ${OWN}.`], ["list", "Only the networks in a list", ""],
        ["set", "Follow a rule set", "Step-by-step rules you write below."], ["none", "Nothing", "They learn no routes from us."],
        ["all", "Everything we know", "Never towards an internet provider."]],
};

/** Why a choice cannot be made here, or null. */
export function choiceBlocker(routing: DeviceRouting, dir: Dir, c: Choice, isp: boolean): string | null {
  if (c.id === "all" && dir === "exp" && isp) return "Not allowed towards an internet provider.";
  if (c.id === "own" && !routing.prefix_lists?.[OWN]) return `Make the network list ${OWN} first (below).`;
  if (c.id === "list" && !c.list) return "Make a network list first (below).";
  if (c.id === "set" && !c.set) return "Make a rule set first (below).";
  return null;
}

/** The document with a plain choice applied to one peer, and the platform's policies nobody uses any more removed. */
export function applyChoice(routing: DeviceRouting, peer: BgpPeer, dir: Dir, c: Choice): DeviceRouting {
  let next = routing;
  let policy: string | null = null;
  if (c.id === "set") policy = c.set ?? null;
  else if (c.id !== "all") {
    policy = autoName(routing, peer, dir);
    let match: string | null = null;
    if (c.id === "default") {
      match = defaultList(peer);
      if (!next.prefix_lists?.[match]) next = putRule(next, "prefix_lists", match, { index: 10, action: "permit", prefix: v6(peer) ? "::/0" : "0.0.0.0/0" });
    } else if (c.id === "own") match = OWN;
    else if (c.id === "list") match = c.list ?? null;
    const lp = dir === "imp" && c.lp && c.lp !== 100 ? c.lp : null;
    const nodes: PolicyNode[] = c.id === "none" ? [{ node: 10, action: "deny" }]
      : [{ node: 10, action: "permit", match_ip_prefix: match, apply_local_preference: lp }, { node: 20, action: "deny" }];
    next = { ...next, route_policies: { ...next.route_policies, [policy]: nodes } };
  }
  next = putItem(next, "bgp_peers", { ...peer, [policyKey(dir)]: policy, [listKey(dir)]: null });
  return prune(next);
}

function prune(routing: DeviceRouting): DeviceRouting {
  const used = new Set([
    ...(routing.bgp_peers ?? []).flatMap((p) => [p.import_policy, p.export_policy]),
    ...(routing.aggregates ?? []).map((a) => a.suppress_policy),
  ]);
  const policies = Object.fromEntries(Object.entries(routing.route_policies ?? {}).filter(([n]) => !isAuto(n) || used.has(n)));
  return { ...routing, route_policies: policies };
}

/** A new connection; towards an internet provider it accepts only the default route from the start. */
export function connect(routing: DeviceRouting, peer: BgpPeer, isp: boolean): DeviceRouting | string {
  if ((routing.bgp_peers ?? []).some((p) => (p.vpn ?? null) === (peer.vpn ?? null) && p.address === peer.address)) return "This router is already connected.";
  const next = putItem(routing, "bgp_peers", peer);
  return isp ? applyChoice(next, peer, "imp", { id: "default" }) : next;
}

/** Remove a connection and the platform's policies only it used. */
export const disconnect = (routing: DeviceRouting, peer: BgpPeer): DeviceRouting =>
  prune({ ...routing, bgp_peers: (routing.bgp_peers ?? []).filter((p) => p !== peer) });

// -- network lists ---------------------------------------------------------------------------

/** One network of a list in words: "10.20.0.0/16 and smaller", "not 10.0.0.0/8". */
export function networkWords(r: PrefixRule): string {
  const inside = r.le != null ? " and smaller" : "";
  return `${r.action === "deny" ? "not " : ""}${r.prefix}${inside}`;
}

/** A network added at the end of a list, "and smaller networks inside it" being less-equal /32 (/128). */
export function addNetwork(routing: DeviceRouting, name: string, prefix: string, inside: boolean): DeviceRouting | string {
  const m = /^([0-9a-fA-F.:]+)\/(\d{1,3})$/.exec(prefix.trim());
  if (!m) return "Write the network like 10.20.0.0/16.";
  const max = m[1].includes(":") ? 128 : 32;
  const rules = routing.prefix_lists?.[name] ?? [];
  if (rules.length && rules[0].prefix.includes(":") !== m[1].includes(":")) return "A list holds IPv4 or IPv6 networks, not both.";
  const index = Math.max(0, ...rules.map((r) => r.index)) + 10;
  return putRule(routing, "prefix_lists", name, { index, action: "permit", prefix: prefix.trim(), le: inside && Number(m[2]) < max ? max : null });
}
export const removeNetwork = (routing: DeviceRouting, name: string, index: number) => removeRule(routing, "prefix_lists", name, index);

/** What uses a network list or a rule set, in words; empty when nothing does. */
export function usedBy(routing: DeviceRouting, name: string, label: (p: BgpPeer) => string): string[] {
  const peers = (routing.bgp_peers ?? []).flatMap((p) => [
    ...(p.import_policy === name || p.import_prefix_list === name ? [`accepting from ${label(p)}`] : []),
    ...(p.export_policy === name || p.export_prefix_list === name ? [`sending to ${label(p)}`] : []),
  ]);
  const sets = Object.entries(routing.route_policies ?? {}).filter(([, nodes]) => nodes.some((n) => n.match_ip_prefix === name))
    .map(([n]) => (isAuto(n) ? `a connection's choice (${n})` : `rule set ${n}`));
  return [...peers, ...sets];
}

// -- rule sets ---------------------------------------------------------------------------------

/** A rule set with its steps renumbered 10, 20, ... in the order given. */
export const setSteps = (routing: DeviceRouting, name: string, steps: PolicyNode[]): DeviceRouting =>
  ({ ...routing, route_policies: { ...routing.route_policies, [name]: steps.map((s, i) => ({ ...s, node: (i + 1) * 10 })) } });

export function deleteName(routing: DeviceRouting, kind: "prefix_lists" | "route_policies", name: string): DeviceRouting {
  const rest: Record<string, unknown> = { ...routing[kind] };
  delete rest[name];
  return { ...routing, [kind]: rest };
}

export const NAME = /^[A-Za-z][A-Za-z0-9_.-]{0,62}$/;

// -- the expert view's route flow ------------------------------------------------------------

export interface GateRule { ok: boolean; text: string; lp?: number | null }

const listShort = (routing: DeviceRouting, name: string) =>
  (routing.prefix_lists?.[name] ?? []).map((r) => `${r.action === "deny" ? "not " : ""}${r.prefix}${r.le != null ? ` ≤/${r.le}` : ""}`).join(", ") || name;

/** What the filter between a peer and the switch lets through, in short rules (first match decides). */
export function gateRules(routing: DeviceRouting, peer: BgpPeer, dir: Dir): GateRule[] {
  const policy = peer[policyKey(dir)], list = peer[listKey(dir)];
  if (!policy && !list) return [{ ok: true, text: dir === "imp" ? "every route" : "every best route" }];
  const rules: GateRule[] = [];
  if (list) rules.push({ ok: true, text: `only ${listShort(routing, list)}` });
  if (policy) {
    const nodes = [...(routing.route_policies?.[policy] ?? [])].sort((a, b) => a.node - b.node);
    for (const n of nodes) rules.push({ ok: n.action === "permit", lp: n.apply_local_preference,
      text: n.match_ip_prefix ? listShort(routing, n.match_ip_prefix) : n.action === "deny" ? "the rest" : "any" });
    if (!nodes.length || nodes[nodes.length - 1].match_ip_prefix) rules.push({ ok: false, text: "the rest" });   // VRP: no match denies
  } else rules.push({ ok: false, text: "the rest" });
  return rules;
}

/** A filter dropped on a peer: a route-policy or a prefix list replaces whatever filtered that direction. */
export const setGate = (routing: DeviceRouting, peer: BgpPeer, dir: Dir, kind: "policy" | "plist" | null, name: string | null): DeviceRouting =>
  prune(putItem(routing, "bgp_peers", { ...peer, [policyKey(dir)]: kind === "policy" ? name : null, [listKey(dir)]: kind === "plist" ? name : null }));
