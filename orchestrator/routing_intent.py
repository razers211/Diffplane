"""General routing intent: BGP peers, route-policies, filters, aggregates, static routes, OSPF.

One document per fabric, in the NetBox config context "routing-<site>" under the key
"routing" (the orchestrator is its only writer; it is intent, not a derived copy):

    {"devices": {"<target>": DeviceRouting}, "retired": {"<target>": DeviceRouting}}

A target is a device, or an M-LAG pair "<leaf>+<leaf>" (names sorted): a pair's objects are
generated identically on both leaves; a paired leaf has no intent of its
own. VRP defines filters and policies per device, so each target carries its own; besides them
the fabric's `library` holds prefix lists and route-policies any target may use: one
definition, written on a switch only while something there uses it. `retired` holds
objects deleted from intent (or derived for a deleted service): generators keep owning them
so the next deploy removes them, and a verified deploy of the device clears them. Hand-made
routing config on a switch is never removed: it is imported into intent.

Only forms verified on a test switch (V300) are accepted. Add a form here
only after it has been tested on the lab switch.

Dual-stack: prefix lists, static routes, BGP peers
and aggregates are IPv4 or IPv6, by their own prefix or address (no field says so); a prefix list
holds one family, a peer's prefix lists are its own family, and an IPv6 object in a VPN instance
needs that instance's IPv6 family (a dual-stack segment's or service's).
"""

import asyncio
import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from generators.fabric import segment_vpn
from orchestrator import documents

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,62}")
COMMUNITY = re.compile(r"\d{1,5}:\d{1,5}")
FORBIDDEN_VPN = ("_management_vpn_",)   # never manage the management plane
UNDERLAY_OSPF = 1                       # owned by the Ospf generator
CONTEXT_KEY = "routing"


def status_of(device: dict | None) -> str | None:
    """A NetBox device's status value (the API gives {"value": ..., "label": ...})."""
    status = (device or {}).get("status")
    return status.get("value") if isinstance(status, dict) else status


def context_name(fabric: str) -> str:
    return f"routing-{fabric}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _name(value: str | None) -> str | None:
    if value is not None and not NAME.fullmatch(value):
        raise ValueError(f"{value!r}: a name is 1-63 letters, digits, '_', '.' or '-', starting with a letter")
    return value


def _vpn(value: str | None) -> str | None:
    if value is None:
        return None
    if value in FORBIDDEN_VPN:
        raise ValueError(f"{value} is the management VPN instance: never managed")
    return _name(value)


Action = Literal["permit", "deny"]


Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def family(value) -> int:
    """4 or 6: an object's address family, from its prefix or address."""
    return value.version


class PrefixRule(Strict):
    index: int = Field(ge=1, le=4294967295)
    action: Action
    prefix: Network
    ge: int | None = Field(default=None, ge=0, le=128)
    le: int | None = Field(default=None, ge=0, le=128)

    @model_validator(mode="after")
    def lengths(self):
        length = self.prefix.prefixlen
        top = self.prefix.max_prefixlen
        if any(v is not None and v > top for v in (self.ge, self.le)):
            raise ValueError(f"greater-equal and less-equal are at most {top} for IPv{self.prefix.version}")
        if self.ge is not None and self.le is None:
            # How V300 displays greater-equal alone is not verified yet.
            raise ValueError("give less-equal with greater-equal (greater-equal alone is not verified on the switch)")
        if self.ge is not None and self.ge < length:
            raise ValueError(f"greater-equal {self.ge} is shorter than the prefix length {length}")
        if self.le is not None and self.le < (self.ge if self.ge is not None else length):
            raise ValueError("less-equal must not be below greater-equal or the prefix length")
        return self


class AsPathRule(Strict):
    index: int = Field(ge=1, le=4294967295)
    action: Action
    regex: str = Field(min_length=1, max_length=255)

    @field_validator("regex")
    @classmethod
    def one_word(cls, value: str) -> str:
        if any(c.isspace() for c in value) or '"' in value:
            raise ValueError("an AS-path regular expression is one word without quotes")
        return value


class CommunityRule(Strict):
    index: int = Field(ge=1, le=4294967295)
    action: Action
    community: str

    @field_validator("community")
    @classmethod
    def form(cls, value: str) -> str:
        if not COMMUNITY.fullmatch(value) or any(int(p) > 65535 for p in value.split(":")):
            raise ValueError(f"{value!r} is not a community (nn:nn)")
        return value


class PolicyNode(Strict):
    node: int = Field(ge=0, le=65535)
    action: Action
    match_ip_prefix: str | None = None
    apply_local_preference: int | None = Field(default=None, ge=0, le=4294967295)

    @field_validator("match_ip_prefix")
    @classmethod
    def valid_name(cls, value):
        return _name(value)


class StaticRoute(Strict):
    vpn: str | None = None   # None: the global table
    prefix: Network
    next_hop: Address | Literal["NULL0"]
    preference: int | None = Field(default=None, ge=1, le=255)

    @field_validator("vpn")
    @classmethod
    def valid_vpn(cls, value):
        return _vpn(value)

    @model_validator(mode="after")
    def one_family(self):
        if self.next_hop != "NULL0" and self.next_hop.version != self.prefix.version:
            raise ValueError(f"static route {self.prefix}: the next hop {self.next_hop} is another address family")
        return self

    @property
    def key(self) -> tuple:
        return ("static", self.vpn, str(self.prefix), str(self.next_hop))


class BgpPeer(Strict):
    vpn: str | None = None
    address: Address
    asn: int = Field(ge=1, le=4294967295)
    import_policy: str | None = None
    export_policy: str | None = None
    import_prefix_list: str | None = None
    export_prefix_list: str | None = None

    @field_validator("vpn")
    @classmethod
    def valid_vpn(cls, value):
        return _vpn(value)

    @field_validator("import_policy", "export_policy", "import_prefix_list", "export_prefix_list")
    @classmethod
    def valid_names(cls, value):
        return _name(value)

    @property
    def key(self) -> tuple:
        return ("peer", self.vpn, str(self.address))


class Aggregate(Strict):
    vpn: str | None = None
    prefix: Network
    detail_suppressed: bool = False
    suppress_policy: str | None = None

    @field_validator("vpn")
    @classmethod
    def valid_vpn(cls, value):
        return _vpn(value)

    @field_validator("suppress_policy")
    @classmethod
    def valid_policy(cls, value):
        return _name(value)

    @model_validator(mode="after")
    def one_suppression(self):
        if self.detail_suppressed and self.suppress_policy:
            raise ValueError("an aggregate is either detail-suppressed or has a suppress-policy, not both")
        return self

    @property
    def key(self) -> tuple:
        return ("aggregate", self.vpn, str(self.prefix))


class OspfProcess(Strict):
    process: int = Field(ge=2, le=65535)   # 1 is the underlay (Ospf generator)
    vpn: str
    router_id: ipaddress.IPv4Address
    areas: list[ipaddress.IPv4Address] = Field(default_factory=list)

    @field_validator("vpn")
    @classmethod
    def valid_vpn(cls, value):
        return _vpn(value)

    @property
    def key(self) -> tuple:
        return ("ospf", self.process)


class PairGateway(Strict):
    """The addressing of a service on an M-LAG pair (the dual-active gateway
    is for services only): a main address per leaf, a shared M-LAG address, in one subnet.

    Given either as `subnet` (.1 for the leaf whose name sorts first, .2 for the other, .3 for the
    M-LAG address) or as `addresses` (leaf -> address) plus `mlag_address`.
    """

    vlan: int = Field(ge=2, le=4094)
    trunk: str
    subnet: ipaddress.IPv4Network | None = None
    addresses: dict[str, ipaddress.IPv4Interface] | None = None
    mlag_address: ipaddress.IPv4Interface | None = None

    @field_validator("trunk")
    @classmethod
    def valid_trunk(cls, value: str) -> str:
        if not re.fullmatch(r"Eth-Trunk\d+", value):
            raise ValueError(f"{value!r}: a service on a pair is carried on an M-LAG Eth-Trunk")
        return value

    @model_validator(mode="after")
    def addressing(self):
        if self.subnet is not None:
            if self.addresses or self.mlag_address:
                raise ValueError("give either a subnet or the addresses, not both")
            if self.subnet.num_addresses < 8:
                raise ValueError("the subnet needs at least 3 hosts and the next hop: /29 or larger")
            return self
        if not self.addresses or self.mlag_address is None or len(self.addresses) != 2:
            raise ValueError("give a subnet, or an address for each of the two leaves and the M-LAG address")
        networks = {a.network for a in self.addresses.values()} | {self.mlag_address.network}
        if len(networks) != 1:
            raise ValueError("the leaves' addresses and the M-LAG address must be in one subnet")
        hosts = [a.ip for a in self.addresses.values()] + [self.mlag_address.ip]
        net = next(iter(networks))
        if len(set(hosts)) != 3 or any(h in (net.network_address, net.broadcast_address) for h in hosts):
            raise ValueError("the three addresses must be distinct hosts of the subnet")
        return self

    def with_addresses(self, leaves: list[str]) -> "PairGateway":
        """The explicit form: a subnet's first hosts for the sorted leaves, then the M-LAG address."""
        if self.subnet is None:
            return self
        hosts = list(self.subnet.hosts())
        length = self.subnet.prefixlen
        first, second = sorted(leaves)
        return PairGateway(vlan=self.vlan, trunk=self.trunk,
                           addresses={first: ipaddress.IPv4Interface(f"{hosts[0]}/{length}"),
                                      second: ipaddress.IPv4Interface(f"{hosts[1]}/{length}")},
                           mlag_address=ipaddress.IPv4Interface(f"{hosts[2]}/{length}"))


class DeviceRouting(Strict):
    prefix_lists: dict[str, list[PrefixRule]] = Field(default_factory=dict)
    as_path_filters: dict[str, list[AsPathRule]] = Field(default_factory=dict)
    community_filters: dict[str, list[CommunityRule]] = Field(default_factory=dict)
    route_policies: dict[str, list[PolicyNode]] = Field(default_factory=dict)
    static_routes: list[StaticRoute] = Field(default_factory=list)
    bgp_peers: list[BgpPeer] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
    ospf: list[OspfProcess] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        for kind in ("prefix_lists", "as_path_filters", "community_filters", "route_policies"):
            for name, rules in getattr(self, kind).items():
                _name(name)
                numbers = [getattr(r, "node", None) if kind == "route_policies" else r.index for r in rules]
                if len(numbers) != len(set(numbers)):
                    raise ValueError(f"{kind} {name}: duplicate index/node numbers")
                if kind == "prefix_lists" and len({r.prefix.version for r in rules}) > 1:
                    raise ValueError(f"prefix list {name}: IPv4 and IPv6 rules mix (VRP keeps them apart: "
                                     "ip ip-prefix and ip ipv6-prefix)")
        for kind in ("static_routes", "bgp_peers", "aggregates", "ospf"):
            keys = [item.key for item in getattr(self, kind)]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{kind}: the same object twice")
        return self

    def check_references(self) -> None:
        """Every policy and filter a live object names exists on the same device. Not for
        retired objects: they only drive removal."""
        for name, nodes in self.route_policies.items():
            for node in nodes:
                if node.match_ip_prefix and node.match_ip_prefix not in self.prefix_lists:
                    raise ValueError(f"route-policy {name} node {node.node}: no prefix list {node.match_ip_prefix}")
        for peer in self.bgp_peers:
            for policy in (peer.import_policy, peer.export_policy):
                if policy and policy not in self.route_policies:
                    raise ValueError(f"peer {peer.address}: no route-policy {policy}")
            for prefix_list in (peer.import_prefix_list, peer.export_prefix_list):
                if prefix_list and prefix_list not in self.prefix_lists:
                    raise ValueError(f"peer {peer.address}: no prefix list {prefix_list}")
                if prefix_list and self.list_family(prefix_list) not in (None, peer.address.version):
                    raise ValueError(f"peer {peer.address}: prefix list {prefix_list} is IPv{self.list_family(prefix_list)}")
        for aggregate in self.aggregates:
            if aggregate.suppress_policy and aggregate.suppress_policy not in self.route_policies:
                raise ValueError(f"aggregate {aggregate.prefix}: no route-policy {aggregate.suppress_policy}")

    def list_family(self, name: str) -> int | None:
        """A prefix list's address family (None: empty or unknown)."""
        rules = self.prefix_lists.get(name) or []
        return rules[0].prefix.version if rules else None

    def vpn_uses6(self) -> dict[str, list[str]]:
        """VPN instance -> its IPv6 objects (they need the instance's IPv6 family)."""
        uses: dict[str, list[str]] = {}
        for vpn, what, value in ([(s.vpn, f"static route {s.prefix}", s.prefix) for s in self.static_routes]
                                 + [(p.vpn, f"BGP peer {p.address}", p.address) for p in self.bgp_peers]
                                 + [(a.vpn, f"aggregate {a.prefix}", a.prefix) for a in self.aggregates]):
            if vpn and value.version == 6:
                uses.setdefault(vpn, []).append(what)
        return uses

    def is_empty(self) -> bool:
        return not any(getattr(self, kind) for kind in type(self).model_fields)

    def vpn_uses(self) -> dict[str, list[str]]:
        """VPN instance -> the objects in it (the global table is not listed)."""
        uses: dict[str, list[str]] = {}
        for vpn, what in ([(s.vpn, f"static route {s.prefix}") for s in self.static_routes]
                          + [(p.vpn, f"BGP peer {p.address}") for p in self.bgp_peers]
                          + [(a.vpn, f"aggregate {a.prefix}") for a in self.aggregates]
                          + [(o.vpn, f"ospf {o.process}") for o in self.ospf]):
            if vpn:
                uses.setdefault(vpn, []).append(what)
        return uses


def _objects(routing: DeviceRouting) -> dict:
    """Every object of a target's routing by identity: (kind, name) or its key."""
    found = {}
    for kind in ("prefix_lists", "as_path_filters", "community_filters", "route_policies"):
        for name, value in getattr(routing, kind).items():
            found[(kind, name)] = [v.model_dump(mode="json") for v in value]
    for kind in ("static_routes", "bgp_peers", "aggregates", "ospf"):
        for item in getattr(routing, kind):
            found[item.key] = item.model_dump(mode="json")
    return found


def clashes(a: DeviceRouting, b: DeviceRouting) -> list[str]:
    """Objects two targets that meet on one switch (a pair and one of its switches) both have,
    differently."""
    mine, theirs = _objects(a), _objects(b)
    return [" ".join(str(k) for k in key if k).replace("_", " ") for key in sorted(mine.keys() & theirs.keys(), key=str)
            if mine[key] != theirs[key]]


def combined(*routings: DeviceRouting) -> DeviceRouting:
    """What a switch of a pair runs: the pair's routing and its own (identical duplicates once)."""
    data: dict = {}
    for routing in routings:
        for kind, value in routing.model_dump(mode="json", exclude_defaults=True).items():
            if isinstance(value, dict):
                data[kind] = {**data.get(kind, {}), **value}
            else:
                data[kind] = [*data.get(kind, []), *(v for v in value if v not in data.get(kind, []))]
    return DeviceRouting.model_validate(data)


# -- the fabric's library ----------------------------------------------------------------------
# Prefix lists and route-policies defined once for the fabric. A switch gets one only while its
# routing uses it (a peer, an aggregate or one of its policies names it, or a library policy it
# uses does); the target's own objects keep their names to themselves (never one in both).
SHARED = ("prefix_lists", "route_policies")


def uses(routing: DeviceRouting) -> tuple[set[str], set[str]]:
    """(prefix lists, route-policies) a target's objects name."""
    policies = {p for peer in routing.bgp_peers for p in (peer.import_policy, peer.export_policy) if p}
    policies |= {a.suppress_policy for a in routing.aggregates if a.suppress_policy}
    lists = {p for peer in routing.bgp_peers for p in (peer.import_prefix_list, peer.export_prefix_list) if p}
    lists |= {n.match_ip_prefix for nodes in routing.route_policies.values() for n in nodes if n.match_ip_prefix}
    return lists, policies


def effective(routing: DeviceRouting, library: DeviceRouting) -> DeviceRouting:
    """What a target runs: its own objects and the library objects they use."""
    lists, policies = uses(routing)
    policies = {p for p in policies if p in library.route_policies and p not in routing.route_policies}
    lists |= {n.match_ip_prefix for p in policies for n in library.route_policies[p] if n.match_ip_prefix}
    lists = {n for n in lists if n in library.prefix_lists and n not in routing.prefix_lists}
    if not (lists or policies):
        return routing
    return routing.model_copy(update={
        "prefix_lists": {**routing.prefix_lists, **{n: library.prefix_lists[n] for n in sorted(lists)}},
        "route_policies": {**routing.route_policies, **{n: library.route_policies[n] for n in sorted(policies)}}})


def without_shared(routing: DeviceRouting, library: DeviceRouting) -> DeviceRouting:
    """A target's routing without its identical copies of library objects (a rollback or an intent
    file gives a target what it ran: the library's objects included)."""
    update = {kind: {n: v for n, v in getattr(routing, kind).items() if getattr(library, kind).get(n) != v}
              for kind in SHARED}
    return routing.model_copy(update=update)


def check_document(devices: dict[str, DeviceRouting], library: DeviceRouting) -> None:
    """The library holds only lists and policies, its policies use its own lists, no target has a
    library name of its own, and every target's references resolve (with the library)."""
    if extra := [k for k in type(library).model_fields if k not in SHARED and getattr(library, k)]:
        raise ValueError(f"the fabric library holds prefix lists and route-policies only (not {', '.join(extra)})")
    for name, nodes in library.route_policies.items():
        for node in nodes:
            if node.match_ip_prefix and node.match_ip_prefix not in library.prefix_lists:
                raise ValueError(f"fabric-wide route-policy {name} node {node.node}: no fabric-wide prefix list "
                                 f"{node.match_ip_prefix}")
    for target, routing in devices.items():
        for kind in SHARED:
            if both := sorted(set(getattr(routing, kind)) & set(getattr(library, kind))):
                what = "prefix list" if kind == "prefix_lists" else "route-policy"
                raise ValueError(f"{target}: {what} {', '.join(both)} is fabric-wide; {target} cannot have its own")
        try:
            effective(routing, library).check_references()
        except ValueError as exc:
            raise ValueError(f"{target}: {exc}") from exc


class LibraryChange(Strict):
    """Library objects to set (or delete: null), by name."""
    prefix_lists: dict[str, list[PrefixRule] | None] = Field(default_factory=dict)
    route_policies: dict[str, list[PolicyNode] | None] = Field(default_factory=dict)

    def applied(self, library: DeviceRouting) -> DeviceRouting:
        data = library.model_dump(mode="json", exclude_defaults=True)
        for kind in SHARED:
            named = dict(data.get(kind) or {})
            for name, value in getattr(self, kind).items():
                if value is None:
                    named.pop(name, None)
                else:
                    named[name] = [v.model_dump(mode="json") for v in value]
            data[kind] = named
        return DeviceRouting.model_validate(data)


class TargetSave(Strict):
    routing: DeviceRouting
    library: LibraryChange | None = None


def used_by(document: "RoutingDocument") -> dict[str, dict[str, list[str]]]:
    """Library object -> the targets whose routing uses it."""
    found: dict[str, dict[str, list[str]]] = {kind: {} for kind in SHARED}
    for target in sorted(document.devices):
        routing = document.effective(target)
        for kind in SHARED:
            for name in getattr(document.library, kind):
                if name in getattr(routing, kind):
                    found[kind].setdefault(name, []).append(target)
    return found


def promoted(document: "RoutingDocument") -> tuple["RoutingDocument", list[str], list[str]]:
    """The one-time move into the library: a list or policy that every target
    having it has identically, and uses there (so every switch keeps writing it), goes to the
    library; the simple view's per-connection policies (*-IN, *-OUT) stay. Returns the document,
    what moved and what stayed (with why). Nothing changes on a switch."""
    devices = document.devices
    moved: dict[str, dict] = {kind: {} for kind in SHARED}
    kept: list[str] = []
    for kind in SHARED:
        what = "prefix list" if kind == "prefix_lists" else "route-policy"
        for name in sorted({n for r in devices.values() for n in getattr(r, kind)}):
            holders = {t: r for t, r in devices.items() if name in getattr(r, kind)}
            values = [getattr(r, kind)[name] for r in holders.values()]
            if kind == "route_policies" and re.search(r"-(IN|OUT)$", name):
                continue
            if any(v != values[0] for v in values):
                kept.append(f"{what} {name}: differs between {', '.join(sorted(holders))}")
                continue
            if kind == "prefix_lists":
                used = all(name in uses(r)[0] for r in holders.values())
            else:
                used = all(name in uses(r)[1] for r in holders.values()) and all(
                    n.match_ip_prefix is None or n.match_ip_prefix in moved["prefix_lists"] for n in values[0])
            if not used:
                kept.append(f"{what} {name}: not used everywhere it is (it would leave those switches)")
                continue
            moved[kind][name] = values[0]
    if not any(moved.values()):
        return document, [], kept
    library = document.library.model_copy(update={k: {**getattr(document.library, k), **moved[k]} for k in SHARED})
    devices = {t: r.model_copy(update={k: {n: v for n, v in getattr(r, k).items() if n not in moved[k]} for k in SHARED})
               for t, r in devices.items()}
    names = [f"{'prefix list' if k == 'prefix_lists' else 'route-policy'} {n}" for k in SHARED for n in moved[k]]
    return document.with_routing(devices, library), names, kept


def vpns_on(device: str, services: list[dict], segments: list[dict], gateways: set[str] | None = None,
            borders: set[str] = frozenset(), ipv6: bool = False) -> set[str]:
    """VPN instances the platform creates on a device, from the orchestrator's view of the
    fabric (the mirror of generators.fabric.platform_vpns): the services hosted there, and the
    routed segments attached there or firewalled by one of those services. In a centralized
    fabric (`gateways`: its gateway pair) every routed segment's, on the gateways only;
    in a symmetric one every routed segment's on its border leaves (`borders`). `ipv6`: only
    those with an IPv6 family (dual-stack segments and services)."""
    local = {s["name"] for s in services if device in s["devices"]}
    # A service sits in its own instance or a shared one; `vrf` absent: older callers.
    hosted = {s.get("vrf") or s["name"] for s in services if device in s["devices"]}
    vpns = {s.get("vrf") or s["name"] for s in services if device in s["devices"] and (s.get("ipv6") or not ipv6)}
    for segment in segments:
        routing = segment.get("routing")
        if not routing or segment.get("bd_id") is None:
            continue
        if ipv6 and not routing.get("ipv6"):
            continue
        if gateways is not None:
            here = device in gateways
        elif device in borders:
            here = True
        else:
            here = (any(a["device"] == device for a in segment["attachments"]) or routing.get("firewall") in local
                    or segment_vpn(segment["name"], segment["bd_id"]) in hosted)
        if here:
            vpns.add(segment_vpn(segment["name"], segment["bd_id"]))
    return vpns


NAMED = ("prefix_lists", "as_path_filters", "community_filters", "route_policies")
KEYED = ("static_routes", "bgp_peers", "aggregates", "ospf")


def retire(old: DeviceRouting, new: DeviceRouting, retired: DeviceRouting) -> DeviceRouting:
    """`retired` plus what `old` has and `new` no longer has (by identity); minus what `new`
    has again (an object back in intent is managed, not retired)."""
    named = {}
    for kind in NAMED:
        merged = dict(getattr(retired, kind))
        merged.update({n: v for n, v in getattr(old, kind).items() if n not in getattr(new, kind)})
        named[kind] = {n: v for n, v in merged.items() if n not in getattr(new, kind)}
    keyed = {}
    for kind in KEYED:
        present = {item.key for item in getattr(new, kind)}
        merged = {item.key: item for item in getattr(retired, kind)}
        merged.update({item.key: item for item in getattr(old, kind) if item.key not in present})
        keyed[kind] = [item for key, item in merged.items() if key not in present]
    # Retired entries are not checked for references: they only drive removal.
    return DeviceRouting.model_construct(**named, **keyed)


class RoutingDocument(Strict):
    devices: dict[str, DeviceRouting] = Field(default_factory=dict)
    library: DeviceRouting = Field(default_factory=DeviceRouting)   # fabric-wide lists and policies
    retired: dict[str, DeviceRouting] = Field(default_factory=dict)
    # Firewall/ISP services deleted from NetBox, per device that hosted them: FirewallServices
    # keeps owning their inter-VPN routes until a verified deploy has removed them.
    retired_services: dict[str, list[str]] = Field(default_factory=dict)
    # Pair targets: the leaves verified since their retired objects were retired. The retired
    # objects are dropped once every active leaf of the pair is in it.
    retired_done: dict[str, list[str]] = Field(default_factory=dict)
    # Eth-Trunks deleted from NetBox, per device: {trunk: [members]}. The Trunks generator keeps
    # owning them until a verified deploy of the device has removed them.
    retired_trunks: dict[str, dict[str, list[str]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self):
        check_document(self.devices, self.library)
        return self

    def device(self, name: str) -> DeviceRouting:
        return self.devices.get(name, DeviceRouting())

    def effective(self, name: str) -> DeviceRouting:
        """What a target runs: its own objects and the library's it uses."""
        return effective(self.device(name), self.library)

    def with_device(self, name: str, routing: DeviceRouting) -> "RoutingDocument":
        return self.with_routing({**self.devices, name: routing}, self.library)

    def with_routing(self, devices: dict[str, DeviceRouting], library: DeviceRouting) -> "RoutingDocument":
        """New targets and library. What a target ran and no longer runs is retired there: an
        object it dropped, or a library object nothing of it uses any more."""
        check_document(devices, library)
        retired_all, done = dict(self.retired), dict(self.retired_done)
        for name in set(self.devices) | set(devices):
            old, new = self.effective(name), effective(devices.get(name, DeviceRouting()), library)
            if old == new:
                continue
            before = self.retired.get(name, DeviceRouting())
            retired_all[name] = retire(old, new, before)
            if retired_all[name] != before:
                done.pop(name, None)   # something newly retired: every leaf must be verified again
        return RoutingDocument.model_construct(
            devices={n: d for n, d in devices.items() if not d.is_empty()},
            library=library,
            retired={n: d for n, d in retired_all.items() if not d.is_empty()},
            retired_services=self.retired_services,
            retired_done=done,
            retired_trunks=self.retired_trunks,
        )

    def with_retired_service(self, device: str, service: str) -> "RoutingDocument":
        names = sorted(set(self.retired_services.get(device, [])) | {service})
        return RoutingDocument.model_construct(devices=self.devices, library=self.library, retired=self.retired,
                                               retired_services={**self.retired_services, device: names},
                                               retired_done=self.retired_done, retired_trunks=self.retired_trunks)

    def with_retired_trunk(self, device: str, trunk: str, members: list[str]) -> "RoutingDocument":
        trunks = {**self.retired_trunks.get(device, {}), trunk: sorted(members)}
        return self.model_copy(update={"retired_trunks": {**self.retired_trunks, device: trunks}})

    def verified(self, device: str, target: str, leaves: set[str]) -> "RoutingDocument":
        """A verified deploy of `device` (its diff is empty). Its retired services are gone. The
        retired objects of its target go once every leaf in `leaves` (the target's active
        devices) has been verified since they were retired."""
        retired, done = dict(self.retired), dict(self.retired_done)
        if target in retired:
            seen = set(done.get(target, [])) | {device}
            if seen >= leaves:
                retired.pop(target)
                done.pop(target, None)
            else:
                done[target] = sorted(seen)
        return RoutingDocument.model_construct(
            devices=self.devices, library=self.library, retired=retired,
            # The device's own, and those of its target's planned leaves: never deployed, never verified.
            retired_services={n: s for n, s in self.retired_services.items()
                              if n != device and (n not in target.split("+") or n in leaves)},
            retired_done=done,
            retired_trunks={n: t for n, t in self.retired_trunks.items()
                            if n != device and (n not in target.split("+") or n in leaves)},
        )

    def without_retired(self, name: str) -> "RoutingDocument":
        """After a verified deploy of a device that is its own target (not in a pair)."""
        return self.verified(name, name, {name})

    def data(self) -> dict:
        return {CONTEXT_KEY: self.model_dump(mode="json", exclude_defaults=True)}


def load(data: dict | None) -> RoutingDocument:
    """The document from a config context's data (empty if there is none)."""
    return RoutingDocument.model_validate((data or {}).get(CONTEXT_KEY) or {})


# -- storage: the fabric's config context ------------------------------------------------------

class RoutingStore:
    """Reads and writes the routing document of a fabric in NetBox (orchestrator only)."""

    def __init__(self, netbox):
        self.nb = netbox

    async def _site(self, fabric: str) -> dict:
        from orchestrator.segments import SegmentError

        site = await self.nb.find_one("dcim/sites", slug=fabric)
        if site is None:
            raise SegmentError(f"no fabric {fabric!r}")
        return site

    async def load(self, fabric: str) -> RoutingDocument:
        await self._site(fabric)
        context = await documents.find(self.nb, context_name(fabric))
        return load((context or {}).get("data"))

    async def save(self, fabric: str, document: RoutingDocument) -> None:
        site = await self._site(fabric)
        name = context_name(fabric)
        existing = await documents.find(self.nb, name)
        data = document.data()
        if existing is None:
            await documents.create(self.nb, name, data, [site["id"]])
        elif existing.get("data") != data:
            await documents.save(self.nb, existing, data)

    async def rename_vpn(self, fabric: str, old: str, new: str) -> list[str]:
        """A segment renamed: intent that names its VPN instance follows. Retired objects
        keep the old name: they describe what is on the switches until a verified deploy."""
        document = await self.load(fabric)
        data = document.data()[CONTEXT_KEY]
        changed = []

        def follow(value, where):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "vpn" and item == old:
                        value[key] = new
                        changed.append(where)
                    else:
                        follow(item, where)
            elif isinstance(value, list):
                for item in value:
                    follow(item, where)

        for target, routing in (data.get("devices") or {}).items():
            follow(routing, target)
        if changed:
            await self.save(fabric, load({CONTEXT_KEY: data}))
        return sorted(set(changed))

    async def _device(self, name: str) -> dict | None:
        return await self.nb.find_one("dcim/devices", name=name)

    async def peer_of(self, device: dict) -> str | None:
        """The other leaf of the device's M-LAG pair (custom field mlag_peer), if any."""
        peer = (device.get("custom_fields") or {}).get("mlag_peer")
        if not peer:
            return None
        if isinstance(peer, dict) and peer.get("name"):
            return peer["name"]
        peer_id = peer.get("id") if isinstance(peer, dict) else peer
        found = [d for d in await self.nb.find("dcim/devices", id=peer_id)]
        return found[0]["name"] if found else None

    async def target_of(self, device: str) -> tuple[str | None, str | None, list[str]]:
        """(fabric, intent target, its devices) of a device: its pair, or itself."""
        found = await self._device(device)
        if found is None:
            return None, None, []
        fabric = (found.get("site") or {}).get("slug")
        peer = await self.peer_of(found)
        if peer is None:
            return fabric, device, [device]
        return fabric, "+".join(sorted((device, peer))), sorted((device, peer))

    async def check_target(self, fabric: str, target: str) -> list[str]:
        """The devices of an intent target: one unpaired device, or the two leaves of a pair."""
        from orchestrator.segments import SegmentError

        names = target.split("+")
        devices = []
        for name in names:
            found = await self._device(name)
            if found is None or (found.get("site") or {}).get("slug") != fabric:
                raise SegmentError(f"{name} is not a device of fabric {fabric}")
            devices.append(found)
        if len(names) == 1:
            # A switch of a pair may also have routing of its own, on it alone.
            return names
        if len(names) != 2 or names != sorted(names) or await self.peer_of(devices[0]) != names[1]:
            raise SegmentError(f"{target} is not an M-LAG pair (two leaves, names sorted, joined by '+')")
        return names

    async def set_device(self, fabric: str, target: str, routing: DeviceRouting,
                         library: LibraryChange | None = None) -> RoutingDocument:
        """Replace a target's routing intent (a device, or an M-LAG pair '<leaf>+<leaf>'), and
        change the fabric's library with it (one write: a new list and the peer using it)."""
        from orchestrator.segments import SegmentError

        devices = await self.check_target(fabric, target)
        for device in devices:
            await self._check_service_peers(fabric, device, routing)
        await self._check_vpns(fabric, target, routing)
        current = await self.load(fabric)
        try:
            shared = library.applied(current.library) if library is not None else current.library
        except ValueError as exc:
            raise SegmentError(f"fabric library: {exc}") from exc
        routing = without_shared(routing, shared)
        # The pair's routing and each switch's own end up on the same switch: no object in both
        # unless identical (one owner per line).
        if len(devices) == 2:
            others = {d: current.device(d) for d in devices}
        else:
            _, pair, _ = await self.target_of(target)
            others = {pair: current.device(pair)} if pair != target else {}
        for other, theirs in others.items():
            if clash := clashes(routing, theirs):
                raise SegmentError(f"{', '.join(clash)} also in the routing of {other}, differently: "
                                   "change or remove it there first")
        try:
            document = current.with_routing({**current.devices, target: routing}, shared)
        except ValueError as exc:
            raise SegmentError(str(exc)) from exc
        await self.save(fabric, document)
        return document

    async def set_all(self, fabric: str, devices: dict, library: dict) -> RoutingDocument:
        """Every target and the library at once (a fabric rollback, an intent file)."""
        from orchestrator.segments import SegmentError

        current = await self.load(fabric)
        try:
            shared = DeviceRouting.model_validate(library or {})
            wanted = {t: without_shared(DeviceRouting.model_validate(r), shared) for t, r in (devices or {}).items()}
            for target, routing in wanted.items():
                if target not in current.devices or routing != current.device(target):
                    for device in await self.check_target(fabric, target):
                        await self._check_service_peers(fabric, device, routing)
                    await self._check_vpns(fabric, target, routing)
            document = current.with_routing(wanted, shared)
        except ValueError as exc:
            raise SegmentError(str(exc)) from exc
        await self.save(fabric, document)
        return document

    async def gateways(self, fabric: str) -> set[str] | None:
        """A centralized fabric's gateway pair, or None for a symmetric fabric."""
        context = await documents.find(self.nb, f"fabric-{fabric}")
        settings = (((context or {}).get("data") or {}).get("fabric") or {})
        if settings.get("architecture") != "centralized":
            return None
        return set(settings.get("gateway_pair") or ())

    async def borders(self, fabric: str) -> set[str]:
        """A symmetric fabric's border leaves: every routed segment's VPN instance is there."""
        if await self.gateways(fabric) is not None:
            return set()
        return {d["name"] for d in await self.nb.find("dcim/devices", site=fabric, role="border-leaf")}

    async def fabric_state(self, fabric: str) -> tuple[list[dict], list[dict]]:
        """The fabric's services and segments, as vpns_on reads them."""
        from orchestrator.routing import Routing
        from orchestrator.segments import Segments

        segments = Segments(self.nb)
        await segments._adopt_legacy()   # may write: not inside the gather (all() then only reads)
        services, every = await asyncio.gather(Routing(self.nb).services(fabric), segments.all(fabric))
        return services, [s for s in every if s["fabric"] == fabric]

    async def leaves(self, target: str, devices: dict[str, dict] | None = None) -> list[str]:
        """The devices of a target that must have its VPN instances: the active ones (a planned
        leaf is never deployed), or all of them while none is active. `devices`: the fabric's
        devices by name, when the caller has them already."""
        names = target.split("+")
        active = [n for n in names
                  if status_of(devices[n] if devices and n in devices else await self._device(n)) == "active"]
        return active or names

    async def vpns(self, fabric: str, target: str, state=None, devices: dict[str, dict] | None = None,
                   gateways=..., borders: set[str] | None = None) -> list[str]:
        """The VPN instances a target's routing objects may use: on every one of its leaves."""
        services, segments = state or await self.fabric_state(fabric)
        if gateways is ...:
            gateways = await self.gateways(fabric)
        if borders is None:
            borders = await self.borders(fabric)
        found = [vpns_on(leaf, services, segments, gateways, borders) for leaf in await self.leaves(target, devices)]
        return sorted(set.intersection(*found)) if found else []

    async def _check_vpns(self, fabric: str, target: str, routing: DeviceRouting) -> None:
        from orchestrator.segments import SegmentError

        uses, uses6 = routing.vpn_uses(), routing.vpn_uses6()
        if not uses:
            return
        services, segments = await self.fabric_state(fabric)
        gateways, borders = await self.gateways(fabric), await self.borders(fabric)
        for leaf in await self.leaves(target):
            have = vpns_on(leaf, services, segments, gateways, borders)
            for vpn, objects in uses.items():
                if vpn not in have:
                    raise SegmentError(f"VPN instance {vpn} is not on {leaf} ({', '.join(objects)}): use the global "
                                       "table, a service hosted there, or a routed segment attached there")
            have6 = vpns_on(leaf, services, segments, gateways, borders, ipv6=True)
            for vpn, objects in uses6.items():
                if vpn not in have6:
                    raise SegmentError(f"VPN instance {vpn} has no IPv6 ({', '.join(objects)}): turn IPv6 on for its "
                                       "segment or service first")

    async def guard(self, fabric: str, what: str, change) -> None:
        """Refuse an intent change (`change(services, segments) -> (services, segments)`, on
        copies) that would remove from a leaf a VPN instance routing intent uses there: the
        generators could not place those objects (phase 7 scope)."""
        import copy

        from orchestrator.segments import SegmentError

        document = await self.load(fabric)
        targets = {t: (r.vpn_uses(), r.vpn_uses6()) for t, r in document.devices.items()}
        targets = {t: uses for t, uses in targets.items() if uses[0]}
        if not targets:
            return
        before = await self.fabric_state(fabric)
        after = change(*copy.deepcopy(before))
        gateways, borders = await self.gateways(fabric), await self.borders(fabric)
        for target, (uses, uses6) in targets.items():
            for leaf in await self.leaves(target):
                gone = vpns_on(leaf, *before, gateways, borders) - vpns_on(leaf, *after, gateways, borders)
                for vpn in sorted(gone & set(uses)):
                    raise SegmentError(f"{what} would remove VPN instance {vpn} from {leaf}, which the routing intent "
                                       f"of {target} uses ({', '.join(uses[vpn])}); change that intent first")
                gone6 = (vpns_on(leaf, *before, gateways, borders, ipv6=True)
                         - vpns_on(leaf, *after, gateways, borders, ipv6=True))
                for vpn in sorted(gone6 & set(uses6)):
                    raise SegmentError(f"{what} would remove the IPv6 of VPN instance {vpn} on {leaf}, which the "
                                       f"routing intent of {target} uses ({', '.join(uses6[vpn])}); change that intent first")

    async def _check_service_peers(self, fabric: str, device: str, routing: DeviceRouting) -> None:
        """A firewall's own session belongs to its service (FirewallBgp): the same peer in intent
        would have two owners (Annet refuses: AclNotExclusiveError)."""
        from orchestrator.segments import SegmentError

        everyone, segments = await self.fabric_state(fabric)
        services = [s for s in everyone if device in s["devices"] and s["kind"] == "firewall"]
        segment_vrfs = {segment_vpn(s["name"], s["bd_id"]) for s in segments if s.get("routing") and s.get("bd_id") is not None}
        firewalls = {(s.get("vrf") or s["name"], ipaddress.ip_address(hop)): s["name"] for s in services
                     for hop in (s["next_hop"], *([s["ipv6"]["next_hop"]] if s.get("ipv6") else []))}
        for peer in routing.bgp_peers:
            if (name := firewalls.get((peer.vpn, ipaddress.ip_address(str(peer.address))))) is not None:
                raise SegmentError(f"{peer.address} in {peer.vpn} is the firewall of service {name}: "
                                   "its session is set on the service (ASN), not here")
        # A firewall in a segment's own instance gives the segment its default route (FirewallServices).
        for route in routing.static_routes:
            if route.vpn and ipaddress.ip_network(str(route.prefix)).prefixlen == 0 and str(route.next_hop) != "NULL0" \
                    and (name := firewalls.get((route.vpn, ipaddress.ip_address(str(route.next_hop))))) is not None \
                    and route.vpn in segment_vrfs:
                raise SegmentError(f"the default route via {route.next_hop} in {route.vpn} is firewall service {name}'s: "
                                   "it is set by making it the segment's firewall, not here")

    async def clear_retired(self, device: str) -> None:
        """A verified deploy left the device with an empty diff: its retired services are gone,
        and its target's retired objects once every active device of the target is verified."""
        fabric, target, devices = await self.target_of(device)
        if fabric is None:
            return
        document = await self.load(fabric)
        if (target not in document.retired and device not in document.retired
                and not set(devices) & set(document.retired_services)
                and not set(devices) & set(document.retired_trunks)):
            return
        active = set()
        for name in devices:
            found = await self._device(name)
            if status_of(found) == "active":
                active.add(name)
        document = document.verified(device, target, active | {device})
        if target != device:   # a paired switch's own routing: retired once it alone is verified
            document = document.verified(device, device, {device})
        await self.save(fabric, document)

    async def retire_trunk(self, fabric: str, device: str, trunk: str, members: list[str]) -> None:
        """A trunk was deleted: it and its members stay owned until a verified deploy."""
        await self.save(fabric, (await self.load(fabric)).with_retired_trunk(device, trunk, members))

    async def retire_service(self, fabric: str, device: str, service: str) -> None:
        """A service was deleted: its routes on `device` stay owned until a verified deploy."""
        await self.save(fabric, (await self.load(fabric)).with_retired_service(device, service))
