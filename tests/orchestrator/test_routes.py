"""Route view: parsing what a test switch returned, the policy simulation,
and the API with a fake switch."""

import asyncio
import ipaddress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import routes as rv
from orchestrator.app import app, get_netbox, get_route_reader
from orchestrator.routing_intent import DeviceRouting, RoutingDocument, RoutingStore
from orchestrator.segments import SegmentError
from seed.model import load
from tests.helpers import seed
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).parent / "data" / "routes"


def run(coro):
    return asyncio.run(coro)


def cli(name: str) -> str:
    return rv.parse_cli((DATA / name).read_text(), 1)[0]


# The routing table form seen on the switch, with the cells the example table had empty filled in the
# same columns (LocPrf, AS path) and a second path of a prefix (no Network on its line).
TABLE = """
 Total Number of Routes: 4
        Network            NextHop                       MED        LocPrf    PrefVal Path/Ogn
 *>     198.51.100.0/24    0.0.0.0                        0                     0       ?
 *>i    10.20.0.0/16       10.10.0.1                      0          200        0       65010 65020i
 * i                       10.10.0.3                      0          100        0       65010 65020i
 *>     203.0.113.0/25     10.150.25.5            4294967295                     0       4200000103?
"""


def test_the_routing_table_of_every_instance():
    routes = rv.parse_rib((DATA / "rib.xml").read_text().split("?>", 1)[1])
    assert len(routes) == 14
    assert {r["vpn"] for r in routes} == {"_public_", "_management_vpn_", "FW"}
    loopback = [r for r in routes if r["prefix"] == "10.10.0.2/32"]
    assert [(r["protocol"], r["active"]) for r in loopback] == [("direct", True), ("ospf", False)]   # inactive kept
    default = next(r for r in routes if r["vpn"] == "_management_vpn_" and r["prefix"] == "0.0.0.0/0")
    assert default["protocol"] == "static" and default["next_hop"] == "172.24.0.254" and default["preference"] == 60


def test_ipv6_routes_follow_the_ipv4_ones():
    """the IPv6 table's entries have the IPv4 leaves (huawei-routing YANG); this entry
    is built from the module, not read from the switch."""
    six = ("<ipv6-unicast-routes><ipv6-unicast-route><prefix>FD00:300::</prefix><mask-length>48</mask-length>"
           "<protocol-type>static</protocol-type><interface-name>NULL0</interface-name><process-id>0</process-id>"
           "<nexthop>::</nexthop><preference>60</preference><cost>0</cost><flag>D</flag><active>true</active>"
           "</ipv6-unicast-route></ipv6-unicast-routes>")
    xml = (DATA / "rib.xml").read_text().split("?>", 1)[1].replace("<ipv4-unicast-routes>", six + "<ipv4-unicast-routes>", 1)
    routes = rv.parse_rib(xml)
    public = [r["prefix"] for r in routes if r["vpn"] == "_public_"]
    assert public[-1] == "FD00:300::/48" and len(routes) == 15
    assert next(r for r in routes if r["prefix"] == "FD00:300::/48")["interface"] == "NULL0"


def test_an_ipv6_prefix_list_matches_ipv6_routes_only():
    routing = DeviceRouting.model_validate({"prefix_lists": {"OWN6": [
        {"index": 10, "action": "permit", "prefix": "fd00:100::/48", "le": 64}]}})
    assert rv.prefix_list(routing, "OWN6", ipaddress.ip_network("fd00:100:0:100::/56"))[0] is True
    assert rv.prefix_list(routing, "OWN6", ipaddress.ip_network("fd00:100::/80"))[0] is False
    assert rv.prefix_list(routing, "OWN6", ipaddress.ip_network("10.0.0.0/8"))[0] is False


def test_ipv6_peers_and_the_ipv6_table_in_blocks():
    """leaf1: IPv6 peers in the IPv4 columns, the IPv6 table one block per route."""
    out = rv.parse_cli((DATA / "bgp-ipv6.xml").read_text(), 6)
    assert rv.parse_peers(out[0]) == [{"address": "FD00:99::2", "asn": 65300, "received": 0, "sent": 0,
                                       "up_down": "00:05:28", "state": "Idle", "prefixes": 0}]
    assert rv.parse_peers(out[3])[0]["address"] == "FD00:255::4" and rv.parse_peers(out[3])[0]["asn"] == 65600
    assert rv.parse_bgp_table(out[1]) == [] and rv.parse_bgp_table(out[5]) == []   # "The peer does not exist."
    [route] = rv.parse_bgp_table(out[4])
    assert route == {"prefix": "FD12:3456:789A:6::/64", "next_hop": "::", "status": "*>", "valid": True, "best": True,
                     "internal": False, "med": 0, "local_preference": None, "preferred_value": 0, "origin": "?",
                     "as_path": ""}
    for command in ("display bgp ipv6 peer", "display bgp vpnv6 vpn-instance FW routing-table",
                    "display bgp ipv6 routing-table peer FD00:99::2 advertised-routes"):
        assert rv.DISPLAY.fullmatch(command)
    assert rv.peer_commands(None, "fd00:99::2")[1] == "display bgp ipv6 routing-table peer FD00:99::2 advertised-routes"
    assert rv._same("FD00:99::2", "fd00:99::2") and not rv._same("FD00:99::2", "10.0.0.1")


def test_a_refused_optional_command_reads_as_empty():
    reply = (DATA / "bgp-ipv6.xml").read_text().replace("<execute-result>success</execute-result>",
                                                        "<execute-result>failed</execute-result>", 1)
    assert rv.parse_cli(reply, 6, frozenset({1}))[0] == ""
    with pytest.raises(SegmentError, match="refused"):
        rv.parse_cli(reply, 6)


def test_bgp_peers_and_a_4_byte_asn_in_dot_form():
    peers = rv.parse_peers(cli("bgp-peer.xml"))
    assert peers == [{"address": "10.150.25.5", "asn": 4200000103, "received": 0, "sent": 0, "up_down": "17:53:16",
                      "state": "Idle", "prefixes": 0}]
    assert rv.local_asn(cli("bgp-peer.xml")) == 65001
    assert rv.asn("65001") == 65001


def test_the_bgp_table_as_the_switch_shows_it():
    routes = rv.parse_bgp_table(cli("fw-table.xml"))
    assert [(r["prefix"], r["next_hop"], r["med"], r["local_preference"], r["preferred_value"], r["origin"], r["best"])
            for r in routes] == [("198.51.100.0/24", "0.0.0.0", 0, None, 0, "?", True),
                                 ("203.0.113.0/25", "0.0.0.0", 0, None, 0, "?", True)]
    assert rv.parse_bgp_table(cli("fw-table-empty.xml")) == []


def test_bgp_table_columns_paths_and_second_paths():
    routes = rv.parse_bgp_table(TABLE)
    assert [(r["prefix"], r["best"], r["internal"], r["local_preference"], r["as_path"], r["origin"]) for r in routes] == [
        ("198.51.100.0/24", True, False, None, "", "?"),
        ("10.20.0.0/16", True, True, 200, "65010 65020", "i"),
        ("10.20.0.0/16", False, True, 100, "65010 65020", "i"),
        ("203.0.113.0/25", True, False, None, "4200000103", "?"),
    ]
    assert routes[1]["next_hop"] == "10.10.0.1" and routes[2]["next_hop"] == "10.10.0.3"
    assert routes[3]["med"] == 4294967295 and routes[3]["preferred_value"] == 0


def test_only_display_commands_are_sent():
    assert "display bgp vpnv4 vpn-instance FW peer" in rv._cli_rpc(["display bgp vpnv4 vpn-instance FW peer"])
    for bad in ("display current-configuration", "undo bgp 65001", "display bgp peer\nsystem-view",
                "display bgp vpnv4 vpn-instance FW;quit routing-table"):
        with pytest.raises(ValueError):
            rv._cli_rpc([bad])
    with pytest.raises(SegmentError):
        rv.bgp_commands("FW peer; undo")
    assert rv.peer_commands("FW", "10.150.25.5")[1] == \
        "display bgp vpnv4 vpn-instance FW routing-table peer 10.150.25.5 advertised-routes"


ROUTING = DeviceRouting.model_validate({
    "prefix_lists": {"OWN": [{"index": 10, "action": "permit", "prefix": "198.51.100.0/24"},
                             {"index": 20, "action": "deny", "prefix": "10.0.0.0/8", "ge": 8, "le": 32},
                             {"index": 30, "action": "permit", "prefix": "0.0.0.0/0", "ge": 8, "le": 24}]},
    "route_policies": {"OUT": [{"node": 10, "action": "permit", "match_ip_prefix": "OWN", "apply_local_preference": 300},
                               {"node": 20, "action": "deny"}]},
    "bgp_peers": [{"vpn": "FW", "address": "10.150.25.5", "asn": 4200000103, "export_policy": "OUT"}],
})
PEER = ROUTING.bgp_peers[0]


def test_prefix_lists_and_route_policies_as_vrp_evaluates_them():
    net = ipaddress.ip_network
    assert rv.prefix_list(ROUTING, "OWN", net("198.51.100.0/24")) == (True, "ip-prefix OWN index 10 permit")
    assert rv.prefix_list(ROUTING, "OWN", net("198.51.100.0/23")) == (True, "ip-prefix OWN index 30 permit")   # 8-24
    assert rv.prefix_list(ROUTING, "OWN", net("10.20.0.0/16")) == (False, "ip-prefix OWN index 20 deny")
    assert rv.prefix_list(ROUTING, "OWN", net("203.0.113.128/25")) == (False, "ip-prefix OWN: no index matched (deny)")
    assert rv.evaluate(ROUTING, PEER, "export", "198.51.100.0/24") == {
        "accepted": True, "by": ["route-policy OUT node 10 permit"], "changes": {"local_preference": 300}}
    assert rv.evaluate(ROUTING, PEER, "export", "10.20.0.0/16") == {
        "accepted": False, "by": ["route-policy OUT node 20 deny"], "changes": {}}
    assert rv.evaluate(ROUTING, PEER, "import", "10.20.0.0/16")["accepted"] is True   # no import policy
    assert rv.evaluate(ROUTING, None, "export", "10.20.0.0/16") == {"accepted": True, "by": [], "changes": {}}


def test_export_simulation_next_to_what_the_switch_advertises():
    rows = rv.simulate_export(ROUTING, PEER, rv.parse_bgp_table(TABLE),
                              [{"prefix": "198.51.100.0/24"}], peer_asn=4200000103, local_asn=65001)
    assert [(r["prefix"], r["simulated"]["accepted"], r["advertised"], r["ibgp_rule"]) for r in rows] == [
        ("198.51.100.0/24", True, True, False),
        ("10.20.0.0/16", False, False, False),
        ("203.0.113.0/25", False, False, False),
    ]   # the second path of 10.20.0.0/16 is not best: not a candidate
    ibgp = rv.simulate_export(DeviceRouting(), None, rv.parse_bgp_table(TABLE), [], peer_asn=65001, local_asn=65001)
    assert [r["ibgp_rule"] for r in ibgp] == [False, True, False]


# -- API ------------------------------------------------------------------------------------------

class FakeReader:
    """Answers like leaf1 did."""

    def __init__(self):
        self.commands = []

    async def read(self, name, rib=False, commands=(), optional=frozenset()):
        self.commands += list(commands)
        outputs = []
        six = rv.parse_cli((DATA / "bgp-ipv6.xml").read_text(), 6)
        for command in commands:
            if " ipv6 " in command or " vpnv6 " in command:   # leaf1's IPv6 answers
                outputs.append((six[3] if "vpnv6" in command else six[0]) if command.endswith(" peer") else "")
            elif command.endswith(" peer"):
                outputs.append(cli("bgp-peer.xml"))
            elif command.endswith("advertised-routes"):
                outputs.append("")
            elif command.endswith("accepted-routes"):
                outputs.append("")
            else:
                outputs.append(cli("fw-table.xml"))
        routes = rv.parse_rib((DATA / "rib.xml").read_text().split("?>", 1)[1]) if rib else None
        return routes, outputs


@pytest.fixture
def example() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    # Written directly: the example fixture has no FW service, which saving through the API would ask for.
    routing = DeviceRouting.model_validate({**ROUTING.model_dump(mode="json"),
                                            "prefix_lists": {"OWN": [{"index": 10, "action": "permit",
                                                                      "prefix": "198.51.100.0/24"}]}})
    run(RoutingStore(netbox).save("example", RoutingDocument(devices={"leaf1+leaf2": routing})))
    return netbox


def test_api_route_view(example):
    reader = FakeReader()
    app.dependency_overrides[get_netbox] = lambda: example
    app.dependency_overrides[get_route_reader] = lambda: reader
    try:
        client = TestClient(app)
        assert len(client.get("/devices/leaf1/routes").json()["routes"]) == 14
        bgp = client.get("/devices/leaf1/bgp?vpn=FW").json()
        assert bgp["target"] == "leaf1+leaf2" and bgp["local_asn"] == 65001
        assert bgp["peers"][0]["intent"]["export_policy"] == "OUT"
        assert [r["prefix"] for r in bgp["routes"]] == ["198.51.100.0/24", "203.0.113.0/25"]
        sim = client.get("/devices/leaf1/bgp/simulation?vpn=FW&peer=10.150.25.5").json()
        assert [(r["prefix"], r["simulated"]["accepted"], r["advertised"]) for r in sim["export"]] == [
            ("198.51.100.0/24", True, False), ("203.0.113.0/25", False, False)]
        assert set(sim["policies"]) == {"OUT", "ip-prefix OWN"} and sim["import"] == []
        assert client.get("/devices/leaf1/bgp/simulation?vpn=FW&peer=10.0.0.9").status_code == 400
        assert client.get("/devices/leaf1/bgp/simulation?vpn=FW&peer=not-an-ip").status_code == 400
        assert client.get("/devices/leaf1/bgp?vpn=bad;name").status_code == 400
    finally:
        for dep in (get_netbox, get_route_reader):
            app.dependency_overrides.pop(dep, None)
    assert "display bgp vpnv4 vpn-instance FW routing-table peer 10.150.25.5 accepted-routes" in reader.commands


def test_only_an_active_pinned_switch_is_read(example):
    with pytest.raises(SegmentError, match="not active"):
        run(rv.RouteReader(example, env={"COLLECTOR_LOGIN": "x"}).target("leaf2"))
    with pytest.raises(SegmentError, match="pinned"):
        run(rv.RouteReader(example, env={"COLLECTOR_LOGIN": "x"}).target("leaf1"))
