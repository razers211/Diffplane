"""Auto-import of hand-made routing config into intent.

Hand-made routing objects are never removed from a switch: the platform adopts them. This
module reads a switch's running config (`annet show current`, parsed with Annet's own VRP
parser), finds the routing objects the platform does not own yet, and returns them as
intent. The running config holds secrets (DFS key, RSA keys, AAA hashes): it is only ever
read in memory here, never stored or returned; reports name objects, never print lines.

Not imported, because the platform owns them by kind or derives them: fabric BGP peers
(LoopBack0 pool), firewall sessions (a firewall service's next hop), inter-VPN static routes,
host routes to fabric loopbacks (M-LAG bypass), OSPF process 1, anything in
`_management_vpn_`, and objects in VPN instances the platform does not create on the device.
Objects whose form the intent schema cannot express (unverified on the switch) are reported
as unmanaged and left alone.

IPv6: the same objects in their IPv6 forms (`ip ipv6-prefix`, `if-match ipv6 address
prefix-list`, `ipv6 route-static`, peers and aggregates in `ipv6-family unicast` / `ipv6-family
vpn-instance <v>` with `ipv6-prefix` filters); a firewall's IPv6 session is the service's.
"""

import asyncio
import ipaddress
from dataclasses import dataclass, field

from pydantic import ValidationError

from generators.fabric import is_segment_vpn
from orchestrator import documents
from orchestrator.routing_intent import DeviceRouting, combined, status_of

MANAGEMENT_VPN = "_management_vpn_"


@dataclass
class Exclusions:
    """What the platform owns on this device other than through routing intent."""

    loopback_pool: ipaddress.IPv4Network | None = None
    pip_pool: ipaddress.IPv4Network | None = None   # the PIP layout: MlagBypass's routes go there
    firewall_next_hops: dict[str, list[str]] = field(default_factory=dict)   # VPN instance -> its firewalls' addresses
    firewall_next_hops6: dict[str, list[str]] = field(default_factory=dict)  # ... their IPv6 addresses
    platform_vpns: set[str] = field(default_factory=set)              # segment instances and services on the device
    dci_peers: set[str] = field(default_factory=set)                  # the switch's DC links' peers
    platform_vpns6: set[str] | None = None                             # ... those with IPv6 (None: as platform_vpns)


@dataclass
class ImportResult:
    routing: DeviceRouting
    unmanaged: list[str] = field(default_factory=list)   # identities only, never config lines

    def is_empty(self) -> bool:
        return self.routing.is_empty()


def parse_tree(text: str) -> dict:
    """The running config as Annet's tree: {row: {child row: {...}}} (VRP indentation rules)."""
    from annet.annlib.netdev.views.hardware import HardwareView
    from annet.vendors import registry_connector, tabparser

    formatter = registry_connector.get().match(HardwareView("Huawei CE6881H-48S6CQ", "")).make_formatter()
    return tabparser.parse_to_tree(text, formatter.split)


def _words(row: str) -> list[str]:
    return row.split()


def _hops(services: list[dict], hop) -> dict[str, list[str]]:
    """VPN instance -> the addresses of the firewalls in it."""
    found: dict[str, list[str]] = {}
    for s in services:
        if s["kind"] == "firewall" and hop(s):
            found.setdefault(s.get("vrf") or s["name"], []).append(hop(s))
    return found


def _same(a: str, b: str | None) -> bool:
    """One address however written (VRP shows IPv6 in upper case, NetBox in lower case)."""
    try:
        return b is not None and ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return False


class _Collector:
    def __init__(self, exclusions: Exclusions):
        self.ex = exclusions
        self.prefix_lists: dict[str, list[dict]] = {}
        self.as_path_filters: dict[str, list[dict]] = {}
        self.community_filters: dict[str, list[dict]] = {}
        self.route_policies: dict[str, list[dict]] = {}
        self.static_routes: list[dict] = []
        self.bgp_peers: dict[tuple, dict] = {}
        self.aggregates: list[dict] = []
        self.ospf: list[dict] = []
        self.unmanaged: list[str] = []
        self.bad_policies: set[str] = set()
        self.bad_peers: set[tuple] = set()

    # -- filters ------------------------------------------------------------------------

    def prefix_list(self, w: list[str]) -> None:
        # ip ip-prefix|ipv6-prefix NAME index I ACTION NET LEN [greater-equal G] [less-equal L]
        try:
            name, index, action, net, length = w[2], int(w[4]), w[5], w[6], int(w[7])
            rule = {"index": index, "action": action, "prefix": f"{net}/{length}"}
            rest = w[8:]
            while rest:
                key, value, rest = rest[0], int(rest[1]), rest[2:]
                rule[{"greater-equal": "ge", "less-equal": "le"}[key]] = value
        except (IndexError, ValueError, KeyError):
            self.unmanaged.append(f"ip-prefix {w[2] if len(w) > 2 else '?'} (form not supported)")
            return
        self.prefix_lists.setdefault(name, []).append(rule)

    def as_path_filter(self, w: list[str]) -> None:
        if len(w) != 7 or w[3] != "index":
            self.unmanaged.append(f"as-path-filter {w[2] if len(w) > 2 else '?'} (form not supported)")
            return
        self.as_path_filters.setdefault(w[2], []).append({"index": int(w[4]), "action": w[5], "regex": w[6]})

    def community_filter(self, w: list[str]) -> None:
        if len(w) != 8 or w[2] != "basic" or w[4] != "index":
            self.unmanaged.append(f"community-filter {w[3] if len(w) > 3 else '?'} (form not supported)")
            return
        self.community_filters.setdefault(w[3], []).append({"index": int(w[5]), "action": w[6], "community": w[7]})

    def route_policy(self, w: list[str], children: dict) -> None:
        # route-policy NAME ACTION node N
        name = w[1]
        node = {"node": int(w[4]), "action": w[2]}
        for child in children:
            c = _words(child)
            if c[:2] == ["if-match", "ip-prefix"] and len(c) == 3:
                node["match_ip_prefix"] = c[2]
            elif c[:4] == ["if-match", "ipv6", "address", "prefix-list"] and len(c) == 5:
                node["match_ip_prefix"] = c[4]   # an IPv6 list
            elif c[:2] == ["apply", "local-preference"] and len(c) == 3:
                node["apply_local_preference"] = int(c[2])
            else:
                self.bad_policies.add(name)
        self.route_policies.setdefault(name, []).append(node)

    # -- static routes -------------------------------------------------------------------

    def static_route(self, w: list[str]) -> None:
        vpn = None
        rest = w[2:]
        if rest[:1] == ["vpn-instance"]:
            vpn, rest = rest[1], rest[2:]
        if vpn == MANAGEMENT_VPN or "vpn-instance" in rest:
            return   # the management plane, or an inter-VPN route (a firewall service's)
        try:
            if w[0] == "ipv6":   # ipv6 route-static [vpn-instance V] NET LEN NH
                net = ipaddress.IPv6Network(f"{rest[0]}/{rest[1]}")
                next_hop = rest[2] if rest[2] == "NULL0" else str(ipaddress.IPv6Address(rest[2]))
            else:
                net = ipaddress.IPv4Network(f"{rest[0]}/{rest[1]}")
                next_hop = rest[2] if rest[2] == "NULL0" else str(ipaddress.IPv4Address(rest[2]))
            route = {"prefix": str(net), "next_hop": next_hop}
            options = rest[3:]
            if options[:1] == ["preference"] and len(options) == 2:
                route["preference"] = int(options[1])
            elif options:
                raise ValueError("options")
        except (IndexError, ValueError):
            self.unmanaged.append(f"static route {' '.join(rest[:2])}{f' in {vpn}' if vpn else ''} (form not supported)")
            return
        if vpn is None and net.prefixlen == 32 and (self._in_pool(net.network_address) or (
                self.ex.pip_pool is not None and net.network_address in self.ex.pip_pool)):
            return   # a host route to a fabric loopback (M-LAG bypass)
        if vpn is not None and vpn not in self.ex.platform_vpns:
            return
        if vpn is not None and net.prefixlen == 0 and is_segment_vpn(vpn) and self._firewall(vpn, next_hop):
            # A firewall in a segment's own instance: that segment's default route (FirewallServices).
            # shortcut: an imported segment named otherwise (an imported fabric's `vrf`) is not recognised; hosting a
            # firewall in one would import its default route into intent.
            return
        if vpn is not None:
            route["vpn"] = vpn
        self.static_routes.append(route)

    def _in_pool(self, address) -> bool:
        try:
            return self.ex.loopback_pool is not None and ipaddress.IPv4Address(address) in self.ex.loopback_pool
        except ValueError:   # e.g. a peer group name: not an address
            return False

    # -- BGP ------------------------------------------------------------------------------

    def _skip_peer(self, vpn: str | None, address: str) -> bool:
        if vpn is None:
            # A fabric peer (BgpEvpn), or a DC link's (Dci).
            return self._in_pool(address) or address.lower() in self.ex.dci_peers
        if vpn == MANAGEMENT_VPN or vpn not in self.ex.platform_vpns:
            return True
        # The firewall's own session, in IPv4 or IPv6.
        return self._firewall(vpn, address)

    def _firewall(self, vpn: str, address: str) -> bool:
        """A firewall service's address in this instance (several when services share it)."""
        return any(_same(address, hop) for hop in [*self.ex.firewall_next_hops.get(vpn, ()),
                                                     *self.ex.firewall_next_hops6.get(vpn, ())])

    def peer_declaration(self, vpn: str | None, c: list[str]) -> None:
        address = c[1]
        if self._skip_peer(vpn, address):
            return
        key = (vpn, address)
        if len(c) == 4 and c[2] == "as-number":
            self.bgp_peers.setdefault(key, {"address": address, **({"vpn": vpn} if vpn else {})})["asn"] = int(c[3])
        else:
            self.bad_peers.add(key)

    def family(self, vpn: str | None, children: dict) -> None:
        for child in children:
            c = _words(child)
            if c[0] == "aggregate":
                self.aggregate(vpn, c)
            elif c[0] == "peer" and len(c) >= 3:
                address = c[1]
                if self._skip_peer(vpn, address):
                    continue
                key = (vpn, address)
                peer = self.bgp_peers.setdefault(key, {"address": address, **({"vpn": vpn} if vpn else {})})
                if c[2:] == ["enable"]:
                    continue
                if len(c) == 5 and c[2] in ("route-policy", "ip-prefix", "ipv6-prefix") and c[4] in ("import", "export"):
                    field_name = f"{c[4]}_{'policy' if c[2] == 'route-policy' else 'prefix_list'}"
                    peer[field_name] = c[3]
                else:
                    self.bad_peers.add(key)

    def aggregate(self, vpn: str | None, c: list[str]) -> None:
        if vpn is not None and vpn not in self.ex.platform_vpns:
            return
        try:
            net = ipaddress.ip_network(f"{c[1]}/{c[2]}")   # IPv4 <net> <mask> or IPv6 <net> <len>
        except (IndexError, ValueError):
            self.unmanaged.append("aggregate (form not supported)")
            return
        aggregate = {"prefix": str(net), **({"vpn": vpn} if vpn else {})}
        options = c[3:]
        if options == ["detail-suppressed"]:
            aggregate["detail_suppressed"] = True
        elif len(options) == 2 and options[0] == "suppress-policy":
            aggregate["suppress_policy"] = options[1]
        elif options:
            self.unmanaged.append(f"aggregate {net}{f' in {vpn}' if vpn else ''} (form not supported)")
            return
        self.aggregates.append(aggregate)

    def bgp(self, children: dict) -> None:
        for child, grandchildren in children.items():
            c = _words(child)
            if c[0] == "peer" and len(c) >= 3:
                self.peer_declaration(None, c)
            elif c in (["ipv4-family", "unicast"], ["ipv6-family", "unicast"]):
                self.family(None, grandchildren)
            elif c[0] == "vpn-instance" and len(c) == 2:
                for row in grandchildren:
                    r = _words(row)
                    if r[0] == "peer" and len(r) >= 3:
                        self.peer_declaration(c[1], r)
            elif c[0] in ("ipv4-family", "ipv6-family") and c[1:2] == ["vpn-instance"] and len(c) == 3:
                self.family(c[2], grandchildren)

    # -- OSPF -----------------------------------------------------------------------------

    def ospf_process(self, w: list[str], children: dict) -> None:
        # ospf N router-id R vpn-instance V
        if len(w) < 2 or not w[1].isdigit() or int(w[1]) == 1:
            return   # the underlay (Ospf generator)
        if len(w) != 6 or w[2] != "router-id" or w[4] != "vpn-instance":
            self.unmanaged.append(f"ospf {w[1]} (form not supported)")
            return
        if w[5] == MANAGEMENT_VPN or w[5] not in self.ex.platform_vpns:
            return
        areas = []
        for child, grandchildren in children.items():
            c = _words(child)
            if c[0] != "area" or len(c) != 2 or grandchildren:
                self.unmanaged.append(f"ospf {w[1]} (form not supported)")
                return
            areas.append(c[1])
        self.ospf.append({"process": int(w[1]), "router_id": w[3], "vpn": w[5], "areas": areas})

    # -- result ---------------------------------------------------------------------------

    def result(self) -> tuple[dict, list[str]]:
        policies = {n: nodes for n, nodes in self.route_policies.items() if n not in self.bad_policies}
        unmanaged = list(self.unmanaged)
        unmanaged += [f"route-policy {n} (a clause not supported)" for n in sorted(self.bad_policies)]
        six = self.ex.platform_vpns if self.ex.platform_vpns6 is None else self.ex.platform_vpns6

        def no_ipv6(item: dict, value: str) -> bool:
            """An IPv6 object in a VPN instance the platform gives no IPv6: left alone."""
            if item.get("vpn") and ":" in value and item["vpn"] not in six:
                unmanaged.append(f"{value} in {item['vpn']} (the instance has no IPv6 in the platform)")
                return True
            return False

        self.static_routes = [r for r in self.static_routes if not no_ipv6(r, r["prefix"])]
        self.aggregates = [a for a in self.aggregates if not no_ipv6(a, a["prefix"])]
        peers = []
        for key, peer in self.bgp_peers.items():
            if no_ipv6(peer, peer["address"]):
                continue
            vpn, address = key
            where = f" in {vpn}" if vpn else ""
            if key in self.bad_peers or "asn" not in peer:
                unmanaged.append(f"BGP peer {address}{where} (an option not supported)")
            else:
                peers.append(peer)
        return {
            "prefix_lists": self.prefix_lists, "as_path_filters": self.as_path_filters,
            "community_filters": self.community_filters, "route_policies": policies,
            "static_routes": self.static_routes, "bgp_peers": peers, "aggregates": self.aggregates,
            "ospf": self.ospf,
        }, unmanaged


def routing_objects(text: str, exclusions: Exclusions) -> ImportResult:
    """Every routing object in a running config that routing intent can express and the
    platform does not own otherwise. Objects already in intent are filtered by `new_objects`."""
    collector = _Collector(exclusions)
    for row, children in parse_tree(text).items():
        w = _words(row)
        if w[:2] in (["ip", "ip-prefix"], ["ip", "ipv6-prefix"]):
            collector.prefix_list(w)
        elif w[:2] == ["ip", "as-path-filter"]:
            collector.as_path_filter(w)
        elif w[:2] == ["ip", "community-filter"]:
            collector.community_filter(w)
        elif w[0] == "route-policy" and len(w) == 5 and w[3] == "node":
            collector.route_policy(w, children)
        elif w[:2] in (["ip", "route-static"], ["ipv6", "route-static"]):
            collector.static_route(w)
        elif w[0] == "bgp" and len(w) == 2 and w[1].isdigit():
            collector.bgp(children)
        elif w[0] == "ospf":
            collector.ospf_process(w, children)
    data, unmanaged = collector.result()
    try:
        routing = DeviceRouting.model_validate(data)
    except ValidationError:
        # One malformed object must not hide the rest: drop what the schema refuses, by kind.
        routing, more = _valid_parts(data)
        unmanaged += more
    return ImportResult(routing, unmanaged)


def _valid_parts(data: dict) -> tuple[DeviceRouting, list[str]]:
    kept, refused = {}, []
    for kind, value in data.items():
        try:
            DeviceRouting.model_validate({kind: value})
            kept[kind] = value
        except ValidationError:
            refused.append(f"{kind.replace('_', ' ')} (a form the intent schema refuses)")
    return DeviceRouting.model_validate(kept), refused


def new_objects(found: DeviceRouting, intent: DeviceRouting, retired: DeviceRouting) -> DeviceRouting:
    """What `found` has that neither intent nor retired has (by identity): intent wins on
    conflicts, and a retired object is being removed, never re-imported."""
    named = {}
    for kind in ("prefix_lists", "as_path_filters", "community_filters", "route_policies"):
        known = set(getattr(intent, kind)) | set(getattr(retired, kind))
        named[kind] = {n: v for n, v in getattr(found, kind).items() if n not in known}
    keyed = {}
    for kind in ("static_routes", "bgp_peers", "aggregates", "ospf"):
        known = {item.key for item in getattr(intent, kind)} | {item.key for item in getattr(retired, kind)}
        keyed[kind] = [item for item in getattr(found, kind) if item.key not in known]
    return DeviceRouting.model_construct(**named, **keyed)


def merged(intent: DeviceRouting, new: DeviceRouting) -> DeviceRouting:
    """Intent plus imported objects, validated as a whole (references included)."""
    data = intent.model_dump(mode="json", exclude_defaults=True)
    extra = new.model_dump(mode="json", exclude_defaults=True)
    for kind, value in extra.items():
        if isinstance(value, dict):
            data[kind] = {**data.get(kind, {}), **value}
        else:
            data[kind] = [*data.get(kind, []), *value]
    return DeviceRouting.model_validate(data)


def drop_dangling(new: DeviceRouting, intent: DeviceRouting) -> tuple[DeviceRouting, list[str]]:
    """Leave out imported objects that name a policy or filter neither intent nor the import
    has (e.g. one in a form not supported), until what is left is consistent."""
    data = new.model_dump(mode="json", exclude_defaults=True)
    dropped = []
    while True:
        prefixes = set(intent.prefix_lists) | set(data.get("prefix_lists", {}))
        policies = set(intent.route_policies) | set(data.get("route_policies", {}))
        changed = False
        for name, nodes in list(data.get("route_policies", {}).items()):
            if any(n.get("match_ip_prefix") and n["match_ip_prefix"] not in prefixes for n in nodes):
                del data["route_policies"][name]
                dropped.append(f"route-policy {name} (uses a prefix list not imported)")
                changed = True
        kept = []
        for peer in data.get("bgp_peers", []):
            missing = [peer.get(k) for k in ("import_policy", "export_policy") if peer.get(k) and peer[k] not in policies]
            missing += [peer.get(k) for k in ("import_prefix_list", "export_prefix_list")
                        if peer.get(k) and peer[k] not in prefixes]
            if missing:
                where = f" in {peer['vpn']}" if peer.get("vpn") else ""
                dropped.append(f"BGP peer {peer['address']}{where} (uses {', '.join(missing)}, not imported)")
                changed = True
            else:
                kept.append(peer)
        data["bgp_peers"] = kept
        aggregates = []
        for aggregate in data.get("aggregates", []):
            if aggregate.get("suppress_policy") and aggregate["suppress_policy"] not in policies:
                dropped.append(f"aggregate {aggregate['prefix']} (uses {aggregate['suppress_policy']}, not imported)")
                changed = True
            else:
                aggregates.append(aggregate)
        data["aggregates"] = aggregates
        if not changed:
            return DeviceRouting.model_validate(data), dropped


def identities(routing: DeviceRouting) -> set:
    """Every object's identity: a name or a key."""
    named = {n for kind in ("prefix_lists", "as_path_filters", "community_filters", "route_policies")
             for n in getattr(routing, kind)}
    return named | {item.key for kind in ("static_routes", "bgp_peers", "aggregates", "ospf")
                    for item in getattr(routing, kind)}


def common(results: list[DeviceRouting], known: set = frozenset()) -> tuple[DeviceRouting, list[str]]:
    """What every leaf of an M-LAG pair has identically (a pair's routing is
    identical on both leaves); objects present on some leaves only, or differing, are reported."""
    if len(results) == 1:
        return results[0], []
    first, others = results[0], results[1:]
    differs = []
    named = {}
    for kind in ("prefix_lists", "as_path_filters", "community_filters", "route_policies"):
        names = set().union(*(set(getattr(r, kind)) for r in results))
        named[kind] = {}
        for name in sorted(names):
            values = [getattr(r, kind).get(name) for r in results]
            if all(v == values[0] for v in values) and values[0] is not None:
                named[kind][name] = values[0]
            elif name not in known:   # in a switch's own routing
                differs.append(f"{kind.replace('_', ' ')[:-1]} {name} (differs between the pair's leaves)")
    keyed = {}
    for kind in ("static_routes", "bgp_peers", "aggregates", "ospf"):
        maps = [{item.key: item for item in getattr(r, kind)} for r in results]
        keyed[kind] = []
        for key in sorted(set().union(*maps), key=str):
            values = [m.get(key) for m in maps]
            if all(v == values[0] for v in values) and values[0] is not None:
                keyed[kind].append(values[0])
            elif key not in known:
                differs.append(f"{kind.replace('_', ' ')[:-1]} {' '.join(str(k) for k in key[1:] if k)} "
                               "(differs between the pair's leaves)")
    return DeviceRouting.model_construct(**named, **keyed), differs


def describe(routing: DeviceRouting) -> list[str]:
    """Identities of the objects, for the audit trail and the report."""
    items = [f"ip-prefix {n}" for n in routing.prefix_lists]
    items += [f"as-path-filter {n}" for n in routing.as_path_filters]
    items += [f"community-filter {n}" for n in routing.community_filters]
    items += [f"route-policy {n}" for n in routing.route_policies]
    items += [f"static {s.prefix}{f' in {s.vpn}' if s.vpn else ''} via {s.next_hop}" for s in routing.static_routes]
    items += [f"peer {p.address}{f' in {p.vpn}' if p.vpn else ''}" for p in routing.bgp_peers]
    items += [f"aggregate {a.prefix}{f' in {a.vpn}' if a.vpn else ''}" for a in routing.aggregates]
    items += [f"ospf {o.process} in {o.vpn}" for o in routing.ospf]
    return items


# -- running it against a switch ---------------------------------------------------------------

def clip(text: str, limit: int = 1200) -> str:
    """A long error kept to its start and its end: Annet prints a traceback whose last lines say
    why it failed (the GUI showed only the start, cut mid-traceback)."""
    if len(text) <= limit:
        return text
    return f"{text[:200]} … {text[-(limit - 203):]}"


@dataclass
class ImportReport:
    device: str
    imported: list[str] = field(default_factory=list)
    unmanaged: list[str] = field(default_factory=list)
    error: str | None = None
    at: float | None = None


class RoutingImporter:
    """Reads a switch and adopts its hand-made routing objects into intent."""

    def __init__(self, netbox, annet, sessions=None):
        from orchestrator.routing_intent import RoutingStore

        self.nb = netbox
        self.annet = annet
        self.sessions = sessions
        self.store = RoutingStore(netbox)
        self.reports: dict[str, ImportReport] = {}
        self._writing = asyncio.Lock()   # imports of several switches run at once (deploy prepare)

    async def exclusions(self, fabric: str, device: str) -> Exclusions:
        from orchestrator.routing_intent import vpns_on

        context = await documents.find(self.nb, f"fabric-{fabric}")
        settings = ((context or {}).get("data") or {}).get("fabric") or {}
        pool, pip = settings.get("loopback_prefix"), settings.get("pip_prefix")
        all_services, segments = await self.store.fabric_state(fabric)
        services = [s for s in all_services if device in s["devices"]]
        gateways, borders = await self.store.gateways(fabric), await self.store.borders(fabric)
        vpns = vpns_on(device, all_services, segments, gateways, borders)
        return Exclusions(
            loopback_pool=ipaddress.IPv4Network(pool) if pool else None,
            pip_pool=ipaddress.IPv4Network(pip) if pip else None,
            firewall_next_hops=_hops(services, lambda s: s["next_hop"]),
            firewall_next_hops6=_hops(services, lambda s: (s.get("ipv6") or {}).get("next_hop")),
            platform_vpns=vpns,
            platform_vpns6=vpns_on(device, all_services, segments, gateways, borders, ipv6=True),
            dci_peers=await self._dci_peers(device),
        )

    async def _dci_peers(self, device: str) -> set[str]:
        from orchestrator import dci

        return dci.peers_of(await dci.document(self.nb), device)

    async def run(self, device: str, user: str = "auto-import") -> ImportReport:
        """Import one device; never raises (an import that fails changes nothing)."""
        import time

        report = ImportReport(device, at=time.time())
        try:
            if self.sessions is not None:
                from orchestrator.deploys import rolling_out

                if (job := await rolling_out(self.sessions)) is not None:
                    report.error = f"deploy #{job} is rolling out: intent is frozen"
                    return self._keep(report)
            fabric, target, devices = await self.store.target_of(device)
            if fabric is None:
                report.error = f"{device} is not in NetBox"
                return self._keep(report)
            # An M-LAG pair: read both leaves. A planned peer (not built) is skipped; an active
            # peer that cannot be read stops the import for the pair.
            peers = [d for d in devices if d != device]
            found = await asyncio.gather(*(self.nb.find_one("dcim/devices", name=peer) for peer in peers))
            peers = [peer for peer, f in zip(peers, found) if status_of(f) == "active"]
            read = await asyncio.gather(*(self.annet.current(d) for d in [device, *peers]), return_exceptions=True)
            if isinstance(read[0], BaseException):
                raise read[0]
            texts = {device: read[0]}
            for peer, text in zip(peers, read[1:]):
                if isinstance(text, BaseException):
                    report.error = f"M-LAG peer {peer} cannot be read ({type(text).__name__}): nothing imported for the pair"
                    return self._keep(report)
                texts[peer] = text
            results = [routing_objects(text, await self.exclusions(fabric, name)) for name, text in sorted(texts.items())]
            async with self._writing:   # load, merge and write the fabric's document as one step
                document = await self.store.load(fabric)
                intent = document.device(target)
                # Each switch's own routing is on one switch: not a difference to report, and
                # never adopted into the pair's.
                own = [document.device(d) for d in texts] + [document.retired.get(d, DeviceRouting()) for d in texts]
                found_routing, differs = common([r.routing for r in results], known=set().union(*map(identities, own)))
                new = new_objects(found_routing, combined(intent, *own[:len(texts)]),
                                  combined(document.retired.get(target, DeviceRouting()), *own[len(texts):]))
                new, dropped = drop_dangling(new, intent)
                if not new.is_empty():
                    await self.store.set_device(fabric, target, merged(intent, new))
            report.unmanaged = sorted({u for r in results for u in r.unmanaged}) + differs + dropped
            if not new.is_empty():
                report.imported = describe(new)
                if self.sessions is not None:
                    from orchestrator.auth import audit

                    await audit(self.sessions, user, "routing.import", detail=f"{target}: {', '.join(report.imported)}")
        except Exception as exc:   # an import is best effort: it never blocks a diff or a deploy
            from orchestrator.redact import redact

            report.error = clip(redact(f"{type(exc).__name__}: {exc}"))
        return self._keep(report)

    def _keep(self, report: ImportReport) -> ImportReport:
        self.reports[report.device] = report
        return report
