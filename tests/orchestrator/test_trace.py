"""Path trace on synthetic tables: two leaf pairs, bridged, peered, through a firewall, out
an ISP, dropped, to a gateway, and the same through a centralized fabric's gateway pair."""

import ipaddress

from orchestrator import trace
from orchestrator.checks import Model
from tests.orchestrator.test_checks import FW, seg

FW3 = {**FW, "device": "leaf3", "devices": ["leaf3"]}
ISP = {**FW, "name": "ISP-A", "kind": "isp", "asn": None, "vlan": 3001, "address": "203.0.113.1/30",
       "next_hop": None, "device": "leaf3", "devices": ["leaf3"]}
STATIC = {"vpn": "web-500", "prefix": "198.51.100.0/24", "next_hop": "203.0.113.2"}


def fabric(architecture="symmetric") -> trace.Fabric:
    segments = [seg("web", 10500, "10.100.0.1/24", "FW", peers=[10600]),
                seg("db", 10600, "10.100.1.1/24", peers=[10500]),
                seg("app", 10700, "10.100.2.1/24")]
    model = Model(segments=segments, services=[FW3, ISP], routing={"devices": {"leaf1+leaf2": {"static_routes": [STATIC]},
                                                                              "spine1+spine2": {"static_routes": [STATIC]}}},
                  pools={"loopbacks": ipaddress.ip_network("10.10.0.0/24")}, planned=set(), ports_down=set(),
                  mlag_trunks=set(), peers={})
    vteps = {"leaf1": "10.20.0.1", "leaf2": "10.20.0.1", "leaf3": "10.20.0.2", "leaf4": "10.20.0.2",
             "spine1": "10.20.0.9", "spine2": "10.20.0.9"}
    return trace.Fabric(model, architecture, ["spine1", "spine2"] if architecture == "centralized" else [], vteps)


TABLES = trace.Tables(
    arp={"leaf1": [{"vpn": "web-500", "ip": "10.100.0.10", "mac": "aaaa-0000-0010", "type": "dynamic-arp", "interface": "Vbdif500"}],
         "leaf3": [{"vpn": "db-600", "ip": "10.100.1.20", "mac": "bbbb-0000-0020", "type": "dynamic-arp", "interface": "Vbdif600"}]},
    macs={"leaf1": [{"bd": 500, "mac": "aaaa-0000-0010", "interface": "10GE1/0/3.1", "peer": None},
                    {"bd": 500, "mac": "cccc-0000-0030", "interface": "Nve1", "peer": "10.20.0.2"}],
          "leaf3": [{"bd": 600, "mac": "bbbb-0000-0020", "interface": "10GE1/0/7.1", "peer": None},
                    {"bd": 500, "mac": "cccc-0000-0030", "interface": "10GE1/0/8.1", "peer": None}]})


def kinds(result):
    return [h["kind"] for h in result["hops"]]


def test_mac_spellings():
    assert trace.mac_of("AA:BB:CC:00:00:10") == trace.mac_of("aabb.cc00.0010") == "aabb-cc00-0010"
    assert trace.mac_of("10.100.0.10") is None and trace.mac_of("aabb-cc00") is None


def test_hosts_are_found_from_arp_then_the_mac_tables_a_local_port_first():
    end = trace.locate(fabric(), TABLES, "10.100.0.10")
    assert (end.segment["name"], end.mac, end.switch, end.port) == ("web", "aaaa-0000-0010", "leaf1", "10GE1/0/3.1")
    far = trace.locate(fabric(), TABLES, "cc:cc:00:00:00:30")
    assert (far.switch, far.port, far.segment["name"]) == ("leaf3", "10GE1/0/8.1", "web")   # not leaf1's Nve entry


def test_one_segment_is_bridged_over_vxlan_between_the_two_vteps():
    r = trace.trace(fabric(), TABLES, "10.100.0.10", "cccc-0000-0030")
    assert r["result"] == trace.DELIVERED and kinds(r) == ["host", "vxlan", "host"]
    assert r["hops"][1]["text"] == "VXLAN VNI 10500 from leaf1 (VTEP 10.20.0.1) to leaf3 (VTEP 10.20.0.2)"
    assert r["switches"] == ["leaf1", "leaf3"]


def test_peered_segments_route_on_the_source_leaf_then_over_the_l3_vni():
    r = trace.trace(fabric(), TABLES, "10.100.0.10", "10.100.1.20")
    assert r["result"] == trace.DELIVERED and kinds(r) == ["host", "gateway", "route", "vxlan", "gateway", "host"]
    assert r["hops"][1]["text"] == "gateway Vbdif500 (10.100.0.1) on leaf1 in web-500"
    assert r["hops"][3]["text"].startswith("L3 VNI 1010600 of db-600 from leaf1")
    assert r["hops"][-1]["text"] == "to 10.100.1.20 (bbbb-0000-0020) on leaf3 10GE1/0/7.1"


def test_anything_else_goes_to_the_firewall_and_ends_there():
    r = trace.trace(fabric(), TABLES, "10.100.0.10", "8.8.8.8")
    assert r["result"] == trace.FIREWALL and kinds(r)[-2:] == ["vxlan", "end"]
    assert r["hops"][-2]["text"] == ("L3 VNI 1010500 of web-500 from leaf1 (VTEP 10.20.0.1) to leaf3 (VTEP 10.20.0.2), "
                                     "the service's leaf")
    assert "firewall 10.255.0.2 behind service FW on leaf3 10GE1/0/4 (VLAN 3000)" in r["hops"][-1]["text"]


def test_a_static_route_to_an_isp_next_hop_leaves_the_fabric():
    r = trace.trace(fabric(), TABLES, "10.100.0.10", "198.51.100.5")
    assert r["result"] == trace.ISP and "static 198.51.100.0/24 via 203.0.113.2" in r["hops"][2]["text"]
    assert r["hops"][-1]["text"].startswith("ISP router 203.0.113.2 behind service ISP-A")


def test_no_route_drops_and_an_unknown_host_is_said_so():
    r = trace.trace(fabric(), TABLES, "10.100.2.5", "10.100.0.10")
    assert r["result"] == trace.DROP
    assert r["hops"][0]["text"] == "from 10.100.2.5 in segment app: not found on any switch port"
    assert r["hops"][-1]["text"] == "no route to 10.100.0.10 in app-700: dropped"


def test_the_lab_case_a_host_not_found_to_its_gateway():
    r = trace.trace(fabric(), trace.Tables(), "10.100.0.10", "10.100.0.1")
    assert r["result"] == trace.GATEWAY and kinds(r) == ["host", "gateway"]
    assert "not found on any switch port" in r["hops"][0]["text"]
    assert r["hops"][1]["text"] == "gateway Vbdif500 (10.100.0.1) on the leaves of web in web-500"


def test_centralized_goes_through_the_gateway_pair_both_ways():
    r = trace.trace(fabric("centralized"), TABLES, "10.100.0.10", "10.100.1.20")
    assert r["result"] == trace.DELIVERED
    assert kinds(r) == ["host", "vxlan", "gateway", "route", "gateway", "vxlan", "host"]
    assert r["hops"][1]["text"] == "VXLAN VNI 10500 from leaf1 (VTEP 10.20.0.1) to spine1 (VTEP 10.20.0.9)"
    assert r["hops"][2]["text"] == "gateway Vbdif500 (10.100.0.1) on spine1+spine2 in web-500"
    assert r["hops"][5]["text"] == "VXLAN VNI 10600 from spine1 (VTEP 10.20.0.9) to leaf3 (VTEP 10.20.0.2)"
    assert r["switches"] == ["leaf1", "spine1", "leaf3"]


def test_tables_parse_as_the_lab_answered():
    from pathlib import Path

    xml = (Path(__file__).parents[1] / "collector" / "fixtures" / "m35-tables.xml").read_text()
    arp, macs = trace.parse_tables(xml)
    assert arp[1] == {"vpn": "_management_vpn_", "ip": "172.24.0.254", "mac": "0200-0000-00f2", "type": "dynamic-arp",
                      "interface": "MEth0/0/0"}
    assert macs == [{"bd": 500, "mac": "aaaa-0000-0010", "type": "dynamic", "interface": "10GE1/0/3.1", "peer": None}]


class Reader:
    """Stands in for the switches: leaf1 has no hosts (as the example)."""

    async def read(self, switches):
        return trace.Tables(arp={s: [] for s in switches}, macs={s: [] for s in switches}), []


def test_on_the_lab_a_host_is_not_found_and_the_gateway_comes_from_intent():
    import asyncio

    from fastapi.testclient import TestClient

    from orchestrator.app import app, get_netbox
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments
    from tests.collector.test_collector import example_netbox

    nb = example_netbox()
    asyncio.run(Segments(nb).create("example", "web", 10500, 500))
    asyncio.run(Routing(nb).set_routing("example", 10500, True, firewall=None))
    web = next(s for s in asyncio.run(Segments(nb).all("example")) if s["name"] == "web")
    gateway = web["routing"]["gateway"].split("/")[0]
    host = str(ipaddress.ip_address(gateway) + 9)
    r = asyncio.run(trace.run(nb, Reader(), "example", host, gateway))
    assert (r["architecture"], r["result"], r["read"]) == ("symmetric", trace.GATEWAY, ["leaf1"])
    assert r["hops"][0]["text"] == f"from {host} in segment web: not found on any switch port"
    assert r["hops"][1]["text"] == f"gateway Vbdif500 ({gateway}) on the leaves of web in web-500"

    app.dependency_overrides[get_netbox] = lambda: nb
    try:
        client = TestClient(app, base_url="https://testserver")
        bad = client.get("/fabrics/example/trace", params={"source": "web", "destination": gateway})
        assert bad.status_code == 400 and "neither an IP address, a MAC address nor a switch" in bad.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_netbox, None)


def test_each_table_is_asked_with_a_filter_ncclient_can_parse():
    """ncclient parses a subtree filter as one XML document: two roots failed on the example."""
    import xml.etree.ElementTree as ET

    class Session:
        asked = []

        def get(self, filter):
            ET.fromstring(filter[1])   # raises on more than one root element, as ncclient does
            self.asked.append(filter[1])
            return type("Reply", (), {"data_xml": '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>'})()

        def close_session(self):
            pass

    session = Session()
    reader = trace.TableReader(type("R", (), {"_connect": lambda self, target: session})())
    assert trace.parse_tables(*reader._read(None)) == ([], [])
    assert [a.split()[0] for a in session.asked] == ["<arp", "<mac", "<ipv6-nd"]


def with_switches(f: trace.Fabric) -> trace.Fabric:
    f.switches = ["leaf1", "leaf2", "leaf3", "leaf4", "spine1", "spine2"]
    return f


def test_a_switch_as_an_end_is_its_gateway_in_a_segment():
    """`<switch>/<segment>`, or the switch alone when it routes one segment."""
    f = with_switches(fabric())
    r = trace.trace(f, TABLES, "leaf1/web", "10.100.1.20")
    assert r["result"] == trace.DELIVERED and r["hops"][0]["text"] == "from leaf1's gateway 10.100.0.1 in segment web"
    assert r["hops"][1]["switch"] == "leaf1"                    # leaf1 routes it itself
    try:
        trace.locate(f, TABLES, "leaf1")
        raise AssertionError("several segments need a choice")
    except ValueError as exc:
        assert "leaf1/<segment>" in str(exc) and "web" in str(exc)
    try:
        trace.check_ends(f, "spine1/web", "10.100.1.20")       # symmetric: no gateway on a spine
        raise AssertionError("refused")
    except ValueError as exc:
        assert "spine1 routes no segment" in str(exc)
    trace.check_ends(f, "leaf1/db", "leaf1/web")
    back = trace.trace(f, TABLES, "10.100.1.20", "leaf1/web")   # anycast: leaf3's own gateway answers
    assert back["result"] == trace.GATEWAY and "not leaf1" in back["hops"][-1]["text"]


def test_a_switch_end_in_a_centralized_fabric_is_on_the_gateway_pair():
    f = with_switches(fabric("centralized"))
    assert trace.locate(f, TABLES, "spine1/web").switch == "spine1"
    try:
        trace.locate(f, TABLES, "leaf1/web")
        raise AssertionError("a layer-2 leaf has no gateway")
    except ValueError as exc:
        assert "leaf1 routes no segment" in str(exc)


def test_switch_ends_are_suggested():
    from orchestrator import reach

    values = {h["value"] for h in reach.known_hosts(with_switches(fabric()), TABLES) if h["kind"] == "switch"}
    assert {"leaf1/web", "leaf1/db"} <= values and not any(v.startswith("spine") for v in values)
