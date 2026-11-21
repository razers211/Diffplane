"""Golden-file tests: generated config for every device of the example fabric.

Regenerate after an intended change with:  UPDATE_GOLDEN=1 pytest tests/generators
and review the diff of tests/golden/ before committing.
"""

import os
from pathlib import Path

import pytest

from tests.generators.harness import devices_from_fabric, render
from tests.helpers import pip_dc, seed

ROOT = Path(__file__).resolve().parents[2]
FABRICS = ["example", "example-central", "example-central-spines", "example-pip"]


async def example_segments(nb):
    """Segments created and attached through the orchestrator's segment service."""
    from orchestrator.segments import Segments

    segments = Segments(nb)
    await segments.create("example", "web", 10500, 500)
    await segments.create("example", "db", 10600, 600)
    await segments.attach("example", "leaf1", "Eth-Trunk1", 10500, 500)   # M-LAG trunk: both leaves
    await segments.attach("example", "leaf1", "10GE1/0/3", 10600, None)    # untagged, leaf1 only


async def example_routing(nb):
    """Routed segments behind a firewall service on leaf1, plus an explicit peering."""
    from orchestrator.routing import Routing

    await example_segments(nb)
    await nb_segments_extra(nb)
    routing = Routing(nb)
    await routing.create_service("example", "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100)
    await routing.set_routing("example", 10500, True)                              # web: default firewall
    await routing.set_routing("example", 10600, True, firewall=None, peers=[10500])  # db: peered with web only
    await routing.set_routing("example", 10700, True)                              # app: firewalled, not attached


async def nb_segments_extra(nb):
    from orchestrator.segments import Segments

    await Segments(nb).create("example", "app", 10700, 700)


async def central_routing(nb):
    """A centralized fabric: web on a leaf trunk behind the firewall on the gateway pair,
    db on another leaf pair peered with web, no firewall."""
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments

    segments, routing = Segments(nb), Routing(nb)
    await segments.create("example-central", "web", 10500, 500)
    await segments.create("example-central", "db", 10600, 600)
    await segments.attach("example-central", "c-leaf1", "Eth-Trunk1", 10500, 500)
    await segments.attach("example-central", "c-leaf3", "10GE1/0/3", 10600, None)
    await routing.create_service("example-central", "FW", "c-gw1", "Eth-Trunk1", 3000, "10.255.0.0/29",
                                 "10.255.0.6", 65100)
    await routing.set_routing("example-central", 10500, True)
    await routing.set_routing("example-central", 10600, True, firewall=None, peers=[10500])


async def spine_routing(nb):
    """A centralized fabric whose gateway pair is the spines: web on a leaf trunk behind
    the firewall on the spines' server trunk."""
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments

    site = "example-central-spines"
    await Segments(nb).create(site, "web", 10500, 500)
    await Segments(nb).attach(site, "s-leaf1", "Eth-Trunk1", 10500, 500)
    await Routing(nb).create_service(site, "FW", "s-spine1", "Eth-Trunk1", 3000, "10.255.0.0/29", "10.255.0.6", 65100)
    await Routing(nb).set_routing(site, 10500, True)


async def example_netstream(nb):
    """NetStream on in the example fabric, leaf1 active with its MEth address; edge ports
    (segments, the FW service) and the uplink to spine1."""
    from orchestrator.fabric_ntp import set_netstream
    from orchestrator.lifecycle import Lifecycle

    await example_routing(nb)
    await Lifecycle(nb).activate("leaf1", "172.24.0.11/24")
    await set_netstream(nb, "example", True, "198.51.100.2", 2055)



async def example_syslog(nb):
    """syslog on in the example fabric to the southbound VIP on a non-default port, leaf1 active
    with its MEth address; an earlier receiver address stays owned (its line would be removed)."""
    from orchestrator.fabric_ntp import set_syslog
    from orchestrator.lifecycle import Lifecycle

    await Lifecycle(nb).activate("leaf1", "172.24.0.11/24")
    await set_syslog(nb, "example", True, "172.24.10.60", None)
    await set_syslog(nb, "example", True, "172.24.10.52", 5514)

async def example_relay(nb):
    """DHCP relay on the example's routed segments: web to a server in its own VPN instance and to
    one behind its firewall FW, db (peered with web) to a server in web."""
    from orchestrator import dhcp_relay

    await example_routing(nb)
    await dhcp_relay.set_pool(nb, "example", "10.10.8.0/24")
    await dhcp_relay.set_relay(nb, "example", 10500, [{"address": "10.100.0.50"}, {"address": "10.200.0.5", "service": "FW"}])
    await dhcp_relay.set_relay(nb, "example", 10600, [{"address": "10.100.0.50", "segment": 10500}])


async def example_microseg(nb):
    """microsegmentation in Learn on the example's dual-stack segments (web firewalled by FW, db
    peered with web, app firewalled): a group of admins inside web (a prefix and a host MAC, inside
    deny), contracts web -> db on TCP 5432-5433 and ICMP, admins -> app for everything."""
    from orchestrator import microseg

    await example_ipv6(nb)
    await microseg.set_mode(nb, "example", "learn")
    epgs = {e["name"]: e for e in (await microseg.get(nb, "example"))["epgs"]}
    web = epgs["web"]
    admins = await microseg.set_group(nb, "example", 10500, "admins", [web["prefixes"][0].rsplit(".", 1)[0] + ".16/28"],
                                      ["02:00:fc:00:00:01"], inside="deny")
    await microseg.set_contract(nb, "example", web["id"], epgs["db"]["id"],
                                [{"protocol": "tcp", "ports": [5432, 5433]}, {"protocol": "icmp"}])
    await microseg.set_contract(nb, "example", admins["id"], epgs["app"]["id"], [{"protocol": "any"}])


async def example_qos(nb):
    """QoS on in the example fabric: segment web (on the M-LAG Eth-Trunk1) marked storage, leaf1's
    10GE1/0/3 (db untagged) marked scavenger; every port of leaf1 and spine1 gets the plan."""
    from orchestrator import qos

    await example_segments(nb)
    await qos.set_enabled(nb, "example", True)
    await qos.set_segment(nb, "example", 10500, "storage")
    await qos.set_port(nb, "example", "leaf1", "10GE1/0/3", "scavenger")


async def example_ports(nb):
    """port settings managed in the example fabric: leaf1's 10GE1/0/3 (db untagged) named and off,
    the M-LAG Eth-Trunk1 (web) named with 9000-byte frames on both leaves, a free port only named."""
    from orchestrator import port_settings
    from orchestrator.fabric_ntp import _update_fabric

    await example_segments(nb)
    await _update_fabric(nb, "example", {"port_profiles": {"enabled": True}})   # turned on (adoption: tests/orchestrator)
    await port_settings.set_port(nb, "leaf1", "10GE1/0/3", description="db-01 (rack A3, NIC 1)", enabled=False)
    await port_settings.set_port(nb, "leaf1", "Eth-Trunk1", description="esxi-01 bond", frame=9000)
    await port_settings.set_port(nb, "leaf1", "10GE1/0/10", description="spare")


async def pip_ports(nb):
    """Copper speed: example-pip (its leaf pair the copper CE6881H-48T6CQ-K) with port settings managed,
    p-leaf1's 10GE1/0/5 fixed at 1 Gbit/s and named, 10GE1/0/6 only named (negotiated)."""
    from orchestrator import port_settings
    from orchestrator.fabric_ntp import _update_fabric
    from seed.model import load

    await seed(nb, pip_dc(copper=True))
    await _update_fabric(nb, "example-pip", {"port_profiles": {"enabled": True}})
    await port_settings.set_port(nb, "p-leaf1", "10GE1/0/5", description="old-nas (1G)", speed=1000)
    await port_settings.set_port(nb, "p-leaf1", "10GE1/0/6", description="esxi-02")


def pip_port_devices():
    import asyncio

    from tests.generators.harness import devices_from_netbox
    from tests.seed.fake_netbox import FakeNetBox

    nb = FakeNetBox()
    asyncio.run(pip_ports(nb))
    return devices_from_netbox(nb, "example-pip")


async def example_protection(nb, mode="on"):
    """protection in the example fabric: the M-LAG Eth-Trunk1 (web) with the Server template on both
    leaves (storm control on its members), leaf1's 10GE1/0/3 (db) with Hypervisor (no edge port)."""
    from orchestrator import protection

    await example_segments(nb)
    await protection.set_mode(nb, "example", mode)
    await protection.set_port(nb, "leaf1", "Eth-Trunk1", "server")
    await protection.set_port(nb, "leaf1", "10GE1/0/3", "hypervisor")


async def example_protection_watch(nb):
    await example_protection(nb, "watch")


DCI_SIDES = [
    {"fabric": "example", "pair": ["leaf1", "leaf2"],
     "uplinks": {"leaf1": [{"port": "100GE1/0/3", "address": "10.255.10.1/30", "neighbour": "10.255.10.2", "asn": 65004}],
                 "leaf2": [{"port": "100GE1/0/3", "address": "10.255.10.5/30", "neighbour": "10.255.10.6", "asn": 65004}]}},
    {"fabric": "example-pip", "pair": ["p-leaf1", "p-leaf2"],
     "uplinks": {"p-leaf1": [{"port": "100GE1/0/3", "address": "10.255.10.2/30", "neighbour": "10.255.10.1", "asn": 65001}],
                 "p-leaf2": [{"port": "100GE1/0/3", "address": "10.255.10.6/30", "neighbour": "10.255.10.5", "asn": 65001}]}},
]


async def example_dci(nb):
    """the example fabric linked to example-pip over dark fibre (one /30 per border switch), segment
    web stretched (VNI 10500 on both sides, the DCI VNI picked: 60000)."""
    from orchestrator import dci
    from orchestrator.segments import Segments
    from seed.model import load

    await seed(nb, pip_dc())
    await example_segments(nb)
    await Segments(nb).create("example-pip", "web", 10500, 500)
    await Segments(nb).attach("example-pip", "p-leaf1", "10GE1/0/3", 10500, None)
    await dci.set_link(nb, "example-pip", DCI_SIDES)
    await dci.verified(nb, "leaf1")   # its deploy of the link, verified: only then a segment stretches
    await dci.set_segment(nb, "example-pip", "web", {"example": 10500, "example-pip": 10500})


def dci_devices(slug: str):
    import asyncio

    from seed.model import load
    from tests.generators.harness import devices_from_netbox
    from tests.seed.fake_netbox import FakeNetBox

    nb = FakeNetBox()
    asyncio.run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    asyncio.run(example_dci(nb))
    return devices_from_netbox(nb, slug)


async def example_ipv6(nb):
    """the example's relaying segments dual-stack: web on SLAAC, db on DHCPv6 with its
    server in web (peered)."""
    from orchestrator import ipv6

    await example_relay(nb)
    await ipv6.set_pool(nb, "example", "fd12:3456:789a::/48")
    await ipv6.set_ipv6(nb, "example", 10500, True)
    await ipv6.set_ipv6(nb, "example", 10600, True, "dhcpv6", [{"address": "fd12:3456:789a:1::53", "segment": 10500}])


async def example_ipv6_services(nb):
    """the example's firewall FW dual-stack (its Vlanif, the IPv6 inter-VPN routes of web,
    eBGP over IPv6), web on DHCPv6 with its server behind FW."""
    from orchestrator import ipv6
    from orchestrator.routing import Routing

    await example_ipv6(nb)
    await Routing(nb).set_service_ipv6("example", "FW", {"address": "fd00:255::/64", "next_hop": "fd00:255::2"})
    await ipv6.set_ipv6(nb, "example", 10500, True, "dhcpv6", [{"address": "fd00:200::5", "service": "FW"}])


async def example_routing6(nb):
    """IPv6 routing intent on the example pair, next to the dual-stack services: an IPv6
    prefix list (less-equal only) and a route-policy matching it, global and VPN static routes,
    global and VPN peers with policies and an IPv6 prefix list, aggregates in both."""
    from orchestrator.routing_intent import DeviceRouting, RoutingStore

    await example_ipv6_services(nb)
    await RoutingStore(nb).set_device("example", "leaf1+leaf2", DeviceRouting.model_validate({
        "prefix_lists": {"OWN6": [{"index": 10, "action": "permit", "prefix": "fd00:100::/48", "le": 64}]},
        "route_policies": {"IN6": [{"node": 10, "action": "permit", "match_ip_prefix": "OWN6",
                                    "apply_local_preference": 200}]},
        "static_routes": [{"prefix": "fd00:300::/48", "next_hop": "NULL0"},
                          {"vpn": "web-500", "prefix": "fd00:301::/48", "next_hop": "fd12:3456:789a:1::9"}],
        "bgp_peers": [{"address": "fd00:99::2", "asn": 65300, "import_policy": "IN6", "export_prefix_list": "OWN6"},
                      {"vpn": "FW", "address": "fd00:255::9", "asn": 65301, "import_policy": "IN6"}],
        "aggregates": [{"prefix": "fd00:100::/48", "detail_suppressed": True},
                       {"vpn": "FW", "prefix": "fd00:400::/48", "detail_suppressed": True}],
    }))


async def central_ipv6(nb):
    """the centralized fabric dual-stack: IPv6 only on the gateway pair (no L3 VNI or IPv6
    route target), web on DHCPv6 with its server in its own instance, db on SLAAC, the peering
    as IPv6 inter-VPN routes too."""
    from orchestrator import ipv6
    from orchestrator.routing import Routing

    await central_routing(nb)
    await ipv6.set_pool(nb, "example-central", "fd12:3456:789b::/48")
    await ipv6.set_ipv6(nb, "example-central", 10500, True, "dhcpv6", [{"address": "fd12:3456:789b:1::53"}])
    await ipv6.set_ipv6(nb, "example-central", 10600, True)
    # FW is the gateway pair's (M-LAG trunk): ::1/::2 per leaf, ::3 the shared M-LAG address.
    await Routing(nb).set_service_ipv6("example-central", "FW", {"address": "fd00:255::/64", "next_hop": "fd00:255::6"})



async def central_microseg(nb):
    """Microsegmentation in a centralized fabric: the gateway pair gets every member, the
    layer-2 leaves the contracts and the MAC members of their bridge domains."""
    from orchestrator import microseg

    site = "example-central"
    await central_routing(nb)
    await microseg.set_mode(nb, site, "enforce")
    epgs = {e["name"]: e for e in (await microseg.get(nb, site))["epgs"]}
    admins = await microseg.set_group(nb, site, 10500, "admins", [epgs["web"]["prefixes"][0].rsplit(".", 1)[0] + ".16/28"],
                                      ["0200-fc00-0001"], inside="deny")
    await microseg.set_contract(nb, site, epgs["web"]["id"], epgs["db"]["id"], [{"protocol": "tcp", "ports": [5432]}])
    await microseg.set_contract(nb, site, admins["id"], epgs["web"]["id"], [{"protocol": "tcp", "ports": [22]}])

CASES = [
    (fabric, device)
    for fabric in FABRICS
    for device in devices_from_fabric(ROOT / "seed" / "fabrics" / f"{fabric}.yaml")
] + [
    ("example-segments", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_segments)
    if device.hostname in ("leaf1", "leaf2")
] + [
    ("example-central-routed", device)
    for device in devices_from_fabric(ROOT / "seed" / "fabrics" / "example-central.yaml", prepare=central_routing)
    if device.hostname not in ("c-spine1", "c-spine2", "c-leaf4")
] + [
    ("example-ipv6-services", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_ipv6_services)
    if device.hostname == "leaf1"
] + [
    ("example-routing6", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_routing6)
    if device.hostname == "leaf1"
] + [
    ("example-central-ipv6", device)
    for device in devices_from_fabric(ROOT / "seed" / "fabrics" / "example-central.yaml", prepare=central_ipv6)
    if device.hostname in ("c-gw1", "c-leaf1")
] + [
    ("example-central-spines-routed", device)
    for device in devices_from_fabric(ROOT / "seed" / "fabrics" / "example-central-spines.yaml", prepare=spine_routing)
    if device.hostname in ("s-spine1", "s-leaf1")
] + [
    ("example-netstream", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_netstream)
    if device.hostname == "leaf1"
] + [
    ("example-syslog", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_syslog)
    if device.hostname == "leaf1"
] + [
    ("example-relay", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_relay)
    if device.hostname in ("leaf1", "leaf2")
] + [
    ("example-ipv6", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_ipv6)
    if device.hostname in ("leaf1", "leaf2")
] + [
    ("example-central-microseg", device)
    for device in devices_from_fabric(ROOT / "seed" / "fabrics" / "example-central.yaml", prepare=central_microseg)
    if device.hostname in ("c-gw1", "c-leaf1", "c-leaf3", "c-spine1")
] + [
    ("example-microseg", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_microseg)
    if device.hostname == "leaf1"
] + [
    ("example-qos", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_qos)
    if device.hostname in ("leaf1", "spine1")
] + [
    ("example-ports", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_ports)
    if device.hostname in ("leaf1", "leaf2", "spine1")
] + [
    ("example-pip-ports", device) for device in pip_port_devices() if device.hostname == "p-leaf1"
] + [
    ("example-protection", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_protection)
    if device.hostname in ("leaf1", "leaf2")
] + [
    ("example-protection-watch", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_protection_watch)
    if device.hostname == "leaf1"
] + [
    ("example-dci", device) for device in dci_devices("example") if device.hostname in ("leaf1", "spine1")
] + [
    ("example-pip-dci", device) for device in dci_devices("example-pip") if device.hostname == "p-leaf1"
] + [
    ("example-routing", device)
    for device in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml", prepare=example_routing)
    if device.hostname in ("leaf1", "leaf2")
]


@pytest.mark.parametrize("fabric, device", CASES, ids=lambda c: getattr(c, "hostname", c))
def test_golden(fabric, device):
    path = ROOT / "tests" / "golden" / fabric / f"{device.hostname}.cfg"
    config = render(device)
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config)
    assert config == path.read_text()
