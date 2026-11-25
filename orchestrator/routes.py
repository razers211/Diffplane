"""Route view: a switch's routing tables and BGP routes, read on demand
when the page is opened, nothing stored, nothing configured.

Where V300 keeps them (a test switch probe):
- the routing table of every instance, inactive routes included, is NETCONF state in
  huawei-network-instance (`.../afs/af/routing/routing-manage/topologys/topology/routes/
  ipv4-unicast-routes`);
- the BGP tables are `deviate not-supported` over NETCONF on this model, so they are read as
  `display bgp ...` text through huawei-cli `execute-command` on the same NETCONF session. Only
  `display` commands built here are ever sent (`DISPLAY`).

"Without policies" is simulated: the platform evaluates its own routing
intent (prefix lists, route-policies) on the routes; the switch is not changed. Export: every
best route of the table is a candidate; the simulation says what the peer's export prefix list
and route-policy do to each, next to what the switch reports as advertised. Import: without
`keep-all-routes` a switch keeps only what its import policy accepted, so rejected routes are
not visible; the accepted ones are shown with what the import policy did to them.

Unverified (no BGP session is up on the test switch): the text of `advertised-routes` and
`accepted-routes` with routes in them (parsed like the routing table, whose form was seen).
"""

import asyncio
import ipaddress
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape

from orchestrator import hostkeys
from orchestrator.netbox import NetBoxClient
from orchestrator.routing_intent import BgpPeer, DeviceRouting
from orchestrator.segments import SegmentError

NI = "urn:huawei:yang:huawei-network-instance"
L3VPN = "urn:huawei:yang:huawei-l3vpn"
ROUTING = "urn:huawei:yang:huawei-routing"
CLI = "urn:huawei:yang:huawei-cli"
RIB_FILTER = (
    f'<network-instance xmlns="{NI}"><instances><instance><name/>'
    f'<afs xmlns="{L3VPN}"><af><routing xmlns="{ROUTING}"><routing-manage><topologys><topology><routes>'
    # the IPv6 table too (same leaves as IPv4: huawei-routing YANG, probe-data/routes).
    "<ipv4-unicast-routes/><ipv6-unicast-routes/></routes></topology></topologys></routing-manage></routing></af></afs>"
    "</instance></instances></network-instance>"
)
# The only commands sent through execute-command: built here from checked values.
# IPv6 (a test switch probe): `display bgp ipv6 ...` and `display bgp vpnv6 vpn-instance <v> ...`.
DISPLAY = re.compile(r"display bgp( vpnv[46] vpn-instance [A-Za-z0-9_.-]+| ipv6)? (peer|routing-table"
                     r"( peer (\d+\.\d+\.\d+\.\d+|[0-9A-F:]+) (advertised-routes|accepted-routes))?)$")
MAX_COMMANDS = 8   # per execute-command (huawei-cli)
MODULE_PARAM = re.compile(r"[?&]module=([^&]+)")   # a capability's YANG module (RFC 6020 hello)
VPN = re.compile(r"[A-Za-z0-9_.-]{1,31}\Z")


# -- parsing ------------------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_rib(data_xml: str) -> list[dict]:
    """Every IPv4 and IPv6 route of every instance from the RIB reply."""
    routes = []
    for instance in ET.fromstring(data_xml).iter(f"{{{NI}}}instance"):
        name = instance.findtext(f"{{{NI}}}name")
        entries = [*instance.iter(f"{{{ROUTING}}}ipv4-unicast-route"), *instance.iter(f"{{{ROUTING}}}ipv6-unicast-route")]
        for entry in entries:
            f = {_local(child.tag): (child.text or "") for child in entry}
            routes.append({
                "vpn": name, "prefix": f"{f.get('prefix')}/{f.get('mask-length')}",
                "protocol": f.get("protocol-type"), "process": int(f.get("process-id") or 0),
                "preference": int(f.get("preference") or 0), "cost": int(f.get("cost") or 0),
                "next_hop": f.get("nexthop"), "interface": f.get("interface-name"),
                "active": f.get("active") == "true", "state": f.get("state"), "flags": f.get("flag"),
                "age": f.get("age"), "tag": int(f.get("tag") or 0),
            })
    def order(r):
        net = ipaddress.ip_network(r["prefix"], strict=False)
        return r["vpn"] or "", net.version, net, not r["active"]   # IPv4 first: the families do not compare

    routes.sort(key=order)
    return routes


def asn(text: str) -> int:
    """A peer AS as VRP displays it: plain, or dot form for 4-byte (64086.60007 = 4200000103)."""
    if "." in text:
        high, low = text.split(".", 1)
        return int(high) * 65536 + int(low)
    return int(text)


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def parse_peers(text: str) -> list[dict]:
    """`display bgp [vpnv4 vpn-instance <v>] peer`."""
    peers = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 9 and _is_address(parts[0]) and parts[1] == "4":
            peers.append({"address": parts[0], "asn": asn(parts[2]), "received": int(parts[3]), "sent": int(parts[4]),
                          "up_down": parts[6], "state": parts[7], "prefixes": int(parts[8])})
    return peers


COLUMNS = ("med", "local_preference", "preferred_value")
_ROUTE = re.compile(r"\d+\.\d+\.\d+\.\d+/\d+$")


_BLOCK = re.compile(r"^\s*(?P<status>[*>dhisSa ]*?)\s*Network\s*:\s*(?P<net>\S+)\s+PrefixLen\s*:\s*(?P<len>\d+)")
_FIELDS = {"NextHop": "next_hop", "MED": "med", "LocPrf": "local_preference", "PrefVal": "preferred_value"}


def _path(route: dict, text: str) -> None:
    """Path/Ogn: the AS path with the origin code last, alone ("?") or attached ("65010 65020i")."""
    text = text.strip()
    route["origin"] = text[-1] if text and text[-1] in "ie?" else None
    route["as_path"] = text[:-1].strip() if route["origin"] else text


def parse_bgp_table6(text: str) -> list[dict]:
    """An IPv6 BGP table: one block per route (`Network : <net> PrefixLen : <len>`, then `NextHop`,
    `MED`, `LocPrf`, `PrefVal` and `Path/Ogn` lines; a test switch, `display bgp vpnv6 vpn-instance FW
    routing-table`). Several paths of one prefix are unverified (one block each, assumed)."""
    routes, route = [], None
    for line in text.splitlines():
        if match := _BLOCK.match(line):
            status = match["status"].strip()
            route = {"prefix": f"{match['net']}/{match['len']}", "next_hop": None, "status": status,
                     "valid": "*" in status, "best": ">" in status, "internal": "i" in status,
                     "med": None, "local_preference": None, "preferred_value": None, "origin": None, "as_path": ""}
            routes.append(route)
            continue
        if route is None:
            continue
        if found := re.search(r"Path/Ogn\s*:(.*)$", line):
            _path(route, found.group(1))
            continue
        for key, value in re.findall(r"(NextHop|MED|LocPrf|PrefVal)\s*:\s*(\S*)", line):
            if value:
                route[_FIELDS[key]] = value if key == "NextHop" else int(value)
    return routes


def parse_bgp_table(text: str) -> list[dict]:
    """A BGP routing table summary (`Network NextHop MED LocPrf PrefVal Path/Ogn`). Empty cells
    are blank, so each value goes to the nearest header column; a line without a network is
    another path of the line above. IPv6 tables come as blocks (parse_bgp_table6)."""
    if "PrefixLen" in text:
        return parse_bgp_table6(text)
    routes, header, network = [], None, None
    for line in text.splitlines():
        if "Network" in line and "NextHop" in line:
            header = {m.group(): (m.start(), m.end()) for m in re.finditer(r"\S+", line)}
            continue
        if header is None or not line.strip():
            continue
        status = line[:header["Network"][0]].strip()
        tokens = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", line[header["Network"][0]:])]
        tokens = [(s + header["Network"][0], e + header["Network"][0], t) for s, e, t in tokens]
        if not tokens or not status or not set(status) <= set("*>dhisSa "):
            continue
        if _ROUTE.match(tokens[0][2]):
            network = tokens.pop(0)[2]
        if network is None or not tokens:
            continue
        route = {"prefix": network, "next_hop": tokens.pop(0)[2], "status": status,
                 "valid": "*" in status, "best": ">" in status, "internal": "i" in status,
                 "med": None, "local_preference": None, "preferred_value": None}
        path_start = header["Path/Ogn"][0]
        centers = {"med": header["MED"], "local_preference": header["LocPrf"], "preferred_value": header["PrefVal"]}
        path = []
        for start, end, token in tokens:
            if start >= path_start - 1:
                path.append(token)
                continue
            middle = (start + end) / 2
            column = min(centers, key=lambda c: abs((centers[c][0] + centers[c][1]) / 2 - middle))
            route[column] = int(token)
        # Path/Ogn: the AS path with the origin code last, alone ("?") or attached ("65010 65020i").
        text = " ".join(path)
        route["origin"] = text[-1] if text and text[-1] in "ie?" else None
        route["as_path"] = text[:-1].strip() if route["origin"] else text
        routes.append(route)
    return routes


# -- simulation ---------------------------------------------------------------------------------

def _prefix_rule(rule, network) -> bool:
    if network.version != rule.prefix.version or not network.subnet_of(rule.prefix):
        return False   # an IPv6 list never matches an IPv4 route, nor the reverse
    low = rule.ge if rule.ge is not None else rule.prefix.prefixlen
    high = rule.le if rule.le is not None else (rule.prefix.max_prefixlen if rule.ge is not None else rule.prefix.prefixlen)
    return low <= network.prefixlen <= high


def prefix_list(routing: DeviceRouting, name: str, network) -> tuple[bool, str]:
    """VRP ip-prefix: the first matching index decides; no match is a deny."""
    for rule in sorted(routing.prefix_lists.get(name, []), key=lambda r: r.index):
        if _prefix_rule(rule, network):
            return rule.action == "permit", f"ip-prefix {name} index {rule.index} {rule.action}"
    return False, f"ip-prefix {name}: no index matched (deny)"


def route_policy(routing: DeviceRouting, name: str, network) -> tuple[bool, str, dict]:
    """VRP route-policy: nodes in order; a node matches when all its if-match clauses do (no
    clause: everything); permit applies its actions and accepts, deny rejects; no node: deny."""
    for node in sorted(routing.route_policies.get(name, []), key=lambda n: n.node):
        if node.match_ip_prefix and not prefix_list(routing, node.match_ip_prefix, network)[0]:
            continue
        if node.action == "deny":
            return False, f"route-policy {name} node {node.node} deny", {}
        changes = {"local_preference": node.apply_local_preference} if node.apply_local_preference is not None else {}
        return True, f"route-policy {name} node {node.node} permit", changes
    return False, f"route-policy {name}: no node matched (deny)", {}


def evaluate(routing: DeviceRouting, peer: BgpPeer | None, direction: str, prefix: str) -> dict:
    """What the peer's prefix list and route-policy in `direction` (import/export) do to a route."""
    network = ipaddress.ip_network(prefix, strict=False)
    if peer is None:
        return {"accepted": True, "by": [], "changes": {}}
    by, changes = [], {}
    prefix_name = peer.import_prefix_list if direction == "import" else peer.export_prefix_list
    policy_name = peer.import_policy if direction == "import" else peer.export_policy
    if prefix_name:
        ok, why = prefix_list(routing, prefix_name, network)
        by.append(why)
        if not ok:
            return {"accepted": False, "by": by, "changes": {}}
    if policy_name:
        ok, why, changes = route_policy(routing, policy_name, network)
        by.append(why)
        if not ok:
            return {"accepted": False, "by": by, "changes": {}}
    return {"accepted": True, "by": by, "changes": changes}


def simulate_export(routing: DeviceRouting, peer: BgpPeer | None, table: list[dict], advertised: list[dict],
                    peer_asn: int | None, local_asn: int | None) -> list[dict]:
    """Every best route of the table, with and without the export policy, next to what the switch
    says it advertises."""
    sent = {r["prefix"] for r in advertised}
    rows = []
    for route in table:
        if not route["best"]:
            continue
        ibgp_rule = route["internal"] and peer_asn is not None and peer_asn == local_asn
        rows.append({**route, "simulated": evaluate(routing, peer, "export", route["prefix"]),
                     "advertised": route["prefix"] in sent,
                     # iBGP routes are not sent to another iBGP peer (unless the switch reflects).
                     "ibgp_rule": ibgp_rule})
    return rows


def simulate_import(routing: DeviceRouting, peer: BgpPeer | None, accepted: list[dict]) -> list[dict]:
    return [{**route, "simulated": evaluate(routing, peer, "import", route["prefix"])} for route in accepted]


# -- reading the switch -------------------------------------------------------------------------

@dataclass
class Target:
    name: str
    address: str
    check: object


def _cli_rpc(commands: list[str]) -> str:
    for command in commands:
        if not DISPLAY.fullmatch(command):
            raise ValueError(f"not a display command the route view sends: {command!r}")
    cmds = "".join(f"<cli-cmd><sequence>{i}</sequence><command>{escape(c)}</command></cli-cmd>"
                   for i, c in enumerate(commands, 1))
    return f'<execute-command xmlns="{CLI}"><cli-cmds>{cmds}</cli-cmds></execute-command>'


def parse_cli(reply_xml: str, count: int, optional: frozenset[int] = frozenset()) -> list[str]:
    """Each command's output (pages joined), in order; a failed command is an error, unless its
    sequence is `optional` (then its output is empty)."""
    pages: dict[int, list[tuple[int, str]]] = {}
    for result in ET.fromstring(reply_xml).iter(f"{{{CLI}}}cli-result"):
        sequence = int(result.findtext(f"{{{CLI}}}sequence") or 0)
        if (result.findtext(f"{{{CLI}}}execute-result") or "") != "success":
            if sequence in optional:
                continue
            raise SegmentError(f"the switch refused `{result.findtext(f'{{{CLI}}}command')}`")
        pages.setdefault(sequence, []).append((int(result.findtext(f"{{{CLI}}}sub-sequence") or 1),
                                               result.findtext(f"{{{CLI}}}cmd-output-info") or ""))
    return ["".join(text for _, text in sorted(pages.get(i, []))) for i in range(1, count + 1)]


class RouteReader:
    """One NETCONF session per page request, with the collector's read account."""

    def __init__(self, netbox: NetBoxClient, env=os.environ):
        self.nb = netbox
        self.login = env.get("COLLECTOR_LOGIN") or env.get("GNETCLI_DEV_LOGIN") or ""
        self.password = env.get("COLLECTOR_PASSWORD") if env.get("COLLECTOR_LOGIN") else env.get("GNETCLI_DEV_PASSWORD")

    async def target(self, name: str) -> Target:
        device = await self.nb.find_one("dcim/devices", name=name)
        if device is None:
            raise SegmentError(f"no device {name!r}")
        status = device.get("status")
        if (status.get("value") if isinstance(status, dict) else status) != "active" or hostkeys.address(device) is None:
            raise SegmentError(f"{name} is not active: its routes cannot be read")
        check = hostkeys.netconf_check(device)
        if check is None:
            raise SegmentError(f"{name} has no pinned NETCONF host key")
        if not self.login:
            raise SegmentError("no read account (COLLECTOR_LOGIN) is set")
        return Target(name, hostkeys.address(device), check)

    def _connect(self, target: Target):
        from ncclient import manager

        return manager.connect(host=target.address, port=hostkeys.NETCONF_PORT, username=self.login,
                               password=self.password, hostkey_verify=True, unknown_host_cb=target.check,
                               allow_agent=False, look_for_keys=False, device_params={"name": "default"}, timeout=60)

    def _run(self, target: Target, rib: bool, commands: list[str],
             optional: frozenset[int] = frozenset()) -> tuple[list[dict] | None, list[str]]:
        from lxml import etree

        session = self._connect(target)
        try:
            routes = parse_rib(session.get(filter=("subtree", RIB_FILTER)).data_xml) if rib else None
            outputs = []
            for i in range(0, len(commands), MAX_COMMANDS):
                chunk = commands[i:i + MAX_COMMANDS]
                reply = session.dispatch(etree.fromstring(_cli_rpc(chunk)))
                outputs += parse_cli(reply.xml, len(chunk), frozenset(n - i for n in optional if i < n <= i + len(chunk)))
            return routes, outputs
        finally:
            try:
                session.close_session()
            except Exception:   # the reply is already read
                pass

    def _capabilities(self, target: Target) -> list[str]:
        session = self._connect(target)
        try:
            return list(session.server_capabilities)
        finally:
            try:
                session.close_session()
            except Exception:   # the hello is already read
                pass

    def _get(self, target: Target, subtree: str | list[str]) -> str | list[str]:
        session = self._connect(target)
        try:
            if isinstance(subtree, list):   # several subtrees in one session (ncclient: one root per filter)
                return [session.get(filter=("subtree", s)).data_xml for s in subtree]
            return session.get(filter=("subtree", subtree)).data_xml
        finally:
            try:
                session.close_session()
            except Exception:   # the reply is already read
                pass

    async def get_state(self, name: str, subtree: str | list[str]) -> str | list[str]:
        """One read-only `<get>` of a state subtree (microsegmentation)."""
        target = await self.target(name)
        try:
            return await asyncio.to_thread(self._get, target, subtree)
        except Exception as exc:   # transport, login, RPC errors: a message for the page
            raise SegmentError(f"reading {name} failed: {type(exc).__name__}: {exc}") from exc

    async def has_module(self, name: str, module: str) -> bool:
        """Whether the switch announces this YANG module in its NETCONF hello."""
        target = await self.target(name)
        try:
            capabilities = await asyncio.to_thread(self._capabilities, target)
        except Exception as exc:   # transport, login: a message for the page
            raise SegmentError(f"reading {name} failed: {type(exc).__name__}: {exc}") from exc
        return module in {m.group(1) for c in capabilities if (m := MODULE_PARAM.search(c))}

    async def read(self, name: str, rib: bool = False, commands: list[str] = (),
                   optional: frozenset[int] = frozenset()) -> tuple[list[dict] | None, list[str]]:
        """`optional`: 1-based positions of commands the switch may refuse (their output is empty)."""
        target = await self.target(name)
        try:
            return await asyncio.to_thread(self._run, target, rib, list(commands), optional)
        except SegmentError:
            raise
        except Exception as exc:   # transport, login, RPC errors: a message for the page
            raise SegmentError(f"reading {name} failed: {type(exc).__name__}: {exc}") from exc


def bgp_commands(vpn: str | None, version: int = 4) -> tuple[str, str]:
    """(peers, table) commands for the global table or a VPN instance, in IPv4 or IPv6."""
    if vpn is not None and not VPN.match(vpn):
        raise SegmentError(f"{vpn!r} is not a VPN instance name")
    if version == 6:
        scope = f" vpnv6 vpn-instance {vpn}" if vpn else " ipv6"
    else:
        scope = f" vpnv4 vpn-instance {vpn}" if vpn else ""
    return f"display bgp{scope} peer", f"display bgp{scope} routing-table"


def peer_commands(vpn: str | None, address: str) -> list[str]:
    ip = ipaddress.ip_address(address)
    _, table = bgp_commands(vpn, ip.version)
    shown = str(ip).upper()   # as the switch displays it (v6: upper case)
    return [table, f"{table} peer {shown} advertised-routes", f"{table} peer {shown} accepted-routes"]


def _same(a: str, b: str) -> bool:
    """Two peer addresses are one (VRP shows IPv6 in upper case, intent stores lower case)."""
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return False


def local_asn(text: str) -> int | None:
    match = re.search(r"Local AS number\s*:\s*(\S+)", text)
    return asn(match.group(1)) if match else None


# -- what the pages ask for ---------------------------------------------------------------------

async def intent_of(store, name: str) -> tuple[str | None, DeviceRouting]:
    """The routing intent that applies to a device: its pair's and its own."""
    from orchestrator.routing_intent import combined

    fabric, target, _ = await store.target_of(name)
    if fabric is None:
        return None, DeviceRouting()
    document = await store.load(fabric)
    if target == name:
        return target, document.device(name)
    return target, combined(document.device(target), document.device(name))


async def routing_table(reader: RouteReader, name: str) -> dict:
    routes, _ = await reader.read(name, rib=True)
    return {"device": name, "routes": routes}


async def bgp(reader: RouteReader, store, name: str, vpn: str | None) -> dict:
    """A BGP table and its peers; each peer says whether routing intent has policies for it."""
    # Both families: an instance without IPv6 may refuse the IPv6 commands (3 and 4).
    commands = [*bgp_commands(vpn), *bgp_commands(vpn, 6)]
    (_, [peers_text, table_text, peers6, table6]), (target, routing) = await asyncio.gather(
        reader.read(name, commands=commands, optional=frozenset({3, 4})), intent_of(store, name))
    intent = [p for p in routing.bgp_peers if p.vpn == vpn]

    def with_intent(peer):
        found = next((p for p in intent if _same(str(p.address), peer["address"])), None)
        return {**peer, "intent": found.model_dump(mode="json") if found else None}

    peers = [with_intent(p) for p in parse_peers(peers_text) + parse_peers(peers6)]
    return {"device": name, "vpn": vpn, "target": target, "local_asn": local_asn(peers_text) or local_asn(peers6),
            "peers": peers, "routes": parse_bgp_table(table_text) + parse_bgp_table(table6)}


async def simulation(reader: RouteReader, store, name: str, vpn: str | None, address: str) -> dict:
    """One peer: export with and without its export policy (and what the switch advertises), and
    the accepted routes with what the import policy did."""
    try:
        commands = peer_commands(vpn, address)
    except ValueError as exc:
        raise SegmentError(f"{address!r} is not an IP address") from exc
    peers_cmd, _ = bgp_commands(vpn, ipaddress.ip_address(address).version)
    (_, [peers_text, table_text, advertised_text, accepted_text]), (target, routing) = await asyncio.gather(
        reader.read(name, commands=[peers_cmd, *commands]), intent_of(store, name))
    state = next((p for p in parse_peers(peers_text) if _same(p["address"], address)), None)
    if state is None:
        raise SegmentError(f"{name} has no BGP peer {address}{f' in {vpn}' if vpn else ''}")
    peer = next((p for p in routing.bgp_peers if p.vpn == vpn and _same(str(p.address), address)), None)
    policies = {}
    if peer is not None:
        for name_ in (peer.import_policy, peer.export_policy):
            if name_:
                policies[name_] = [n.model_dump(mode="json") for n in routing.route_policies.get(name_, [])]
        for name_ in {peer.import_prefix_list, peer.export_prefix_list,
                      *(n.match_ip_prefix for p in (peer.import_policy, peer.export_policy) if p
                        for n in routing.route_policies.get(p, []))} - {None}:
            policies[f"ip-prefix {name_}"] = [r.model_dump(mode="json") for r in routing.prefix_lists.get(name_, [])]
    return {
        "device": name, "vpn": vpn, "target": target, "peer": state,
        "intent": peer.model_dump(mode="json") if peer is not None else None, "policies": policies,
        "export": simulate_export(routing, peer, parse_bgp_table(table_text), parse_bgp_table(advertised_text),
                                  state["asn"], local_asn(peers_text)),
        "import": simulate_import(routing, peer, parse_bgp_table(accepted_text)),
    }
