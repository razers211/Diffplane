"""Rollback planning (a): from today's intent back to a rollback point, in an order the
orchestrator's checks accept; per switch or for the whole fabric. Nothing is written."""

import asyncio
import copy

from orchestrator import db
from orchestrator.intent_snapshots import IntentSnapshots
from orchestrator.rollback import Scope, plan, preview
from orchestrator.segments import Segments
from tests.collector.test_collector import example_netbox


def run(coro):
    return asyncio.run(coro)


PEERS = {"leaf1": "leaf2", "leaf2": "leaf1"}
FW = {"name": "FW", "device": "leaf1", "port": "10GE1/0/4", "vlan": 3000, "address": "10.255.0.1/30",
      "next_hop": "10.255.0.2", "asn": 65100, "kind": "firewall", "addresses": None}


def intent(**parts) -> dict:
    base = {"version": 1, "segments": [], "services": [], "routing": {}, "trunks": {}, "fabric": {}}
    return {**base, **parts}


def seg(name, vni, bd, routing=None, attachments=()):
    return {"name": name, "vni": vni, "bd_id": bd, "routing": routing,
            "attachments": [{"device": d, "interface": i, "vid": v} for d, i, v in attachments]}


def kinds(result):
    return [(o["kind"], o.get("vni") or o.get("name") or o.get("target") or o.get("device")) for o in result["operations"]]


WEB = seg("web", 10500, 500, {"gateway": "10.100.0.1/24", "peers": [], "firewall": "FW"}, [("leaf1", "10GE1/0/3", None)])


def test_a_segment_added_since_goes_in_order():
    now = intent(segments=[WEB], services=[FW])
    then = intent(services=[FW])
    assert kinds(plan(now, then, Scope(peers=PEERS))) == [("detach", 10500), ("unroute", 10500), ("delete_segment", 10500)]


def test_a_segment_deleted_since_comes_back_with_its_exact_gateway():
    result = plan(intent(services=[FW]), intent(segments=[WEB], services=[FW]), Scope(peers=PEERS))
    assert kinds(result) == [("create_segment", 10500), ("route", 10500), ("attach", 10500)]
    route = result["operations"][1]
    assert route["subnet"] == "10.100.0.1/24" and route["firewall"] == "FW"


def test_rename_vid_and_fabric_settings():
    now = intent(segments=[seg("frontend", 10500, 500, None, [("leaf1", "10GE1/0/3", 7)])], fabric={"snmp": {"location": "b"}})
    then = intent(segments=[seg("web", 10500, 500, None, [("leaf1", "10GE1/0/3", None)])], fabric={"snmp": {"location": "a"}})
    assert kinds(plan(now, then, Scope(peers=PEERS))) == [("set_vid", 10500), ("rename", 10500), ("snmp", None)]


def test_this_switch_only_touches_its_own_intent():
    other = seg("db", 10600, 600, None, [("leaf3", "10GE1/0/3", None)])
    now = intent(segments=[seg("web2", 10500, 500, None, []), other])
    then = intent(segments=[seg("web", 10500, 500, None, [("leaf1", "10GE1/0/3", None)]),
                            seg("app", 10700, 700, None, [("leaf1", "10GE1/0/5", 70), ("leaf3", "10GE1/0/9", None)])])
    result = plan(now, then, Scope(devices={"leaf1", "leaf2"}, target="leaf1+leaf2", peers=PEERS))
    # app is recreated because leaf1 needs it; leaf3's attachments and db are not this switch's.
    assert kinds(result) == [("create_segment", 10700), ("attach", 10500), ("attach", 10700)]
    assert "web2" in result["notes"][0] and "fabric rollback" in result["notes"][0]


def test_an_mlag_trunk_attachment_is_one_operation():
    trunk = {"leaf1": [{"number": 1, "members": ["10GE1/0/1"], "m_lag": True}],
             "leaf2": [{"number": 1, "members": ["10GE1/0/1"], "m_lag": True}]}
    then = intent(segments=[seg("web", 10500, 500, None, [("leaf1", "Eth-Trunk1", 500), ("leaf2", "Eth-Trunk1", 500)])], trunks=trunk)
    now = intent(segments=[seg("web", 10500, 500, None, [])], trunks=trunk)
    assert kinds(plan(now, then, Scope(peers=PEERS))) == [("attach", 10500)]


def test_trunks_go_before_and_come_after_and_an_mlag_trunk_is_one_operation():
    now = intent(trunks={"leaf1": [{"number": 2, "members": ["10GE1/0/5"], "m_lag": False}]})
    then = intent(trunks={d: [{"number": 3, "members": ["10GE1/0/7"], "m_lag": True}] for d in ("leaf1", "leaf2")})
    result = plan(now, then, Scope(peers=PEERS))
    assert kinds(result) == [("delete_trunk", "leaf1"), ("create_trunk", "leaf1")]
    assert result["operations"][1]["mlag"] is True


def test_routing_objects_in_vpn_instances_leave_first_and_come_back_last():
    vpn_route = {"vpn": "web-500", "prefix": "10.9.0.0/16", "next_hop": "NULL0"}
    global_route = {"vpn": None, "prefix": "10.8.0.0/16", "next_hop": "NULL0"}
    now = intent(segments=[WEB], services=[FW],
                 routing={"devices": {"leaf1+leaf2": {"static_routes": [vpn_route, global_route]}}})
    then = intent(services=[FW], routing={"devices": {"leaf1+leaf2": {"static_routes": [global_route]}}})
    result = plan(now, then, Scope(peers=PEERS))
    assert kinds(result)[0] == ("routing_set", "leaf1+leaf2") and kinds(result)[-1] == ("routing_set", "leaf1+leaf2")
    assert result["operations"][0]["routing"] == {"static_routes": [global_route]}
    assert ("delete_segment", 10500) in kinds(result)


def test_a_replaced_service_frees_its_segments_first():
    now = intent(segments=[WEB], services=[{**FW, "vlan": 3001}])
    then = intent(segments=[WEB], services=[FW])
    result = plan(now, then, Scope(peers=PEERS))
    assert kinds(result) == [("route", 10500), ("delete_service", "FW"), ("create_service", None), ("route", 10500)]
    assert result["operations"][0]["firewall"] is None and result["operations"][-1]["firewall"] == "FW"
    asn_only = plan(intent(services=[{**FW, "asn": 65101}]), intent(services=[FW]), Scope(peers=PEERS))
    assert kinds(asn_only) == [("service_asn", "FW")]


def test_services_in_existing_instances_come_back_after_what_they_join():
    """a service joining another's instance after it; one in a segment's
    instance once the segment is routed, then made that segment's firewall."""
    isp = {**FW, "name": "AAA", "port": "10GE1/0/5", "vlan": 3002, "kind": "isp", "asn": None, "vrf": "FW"}
    inside = {**FW, "name": "IN", "port": "10GE1/0/6", "vlan": 3004, "asn": None, "vrf": "web-500"}
    web = seg("web", 10500, 500, {"gateway": "10.100.0.1/24", "peers": [], "firewall": "IN"})
    ops = plan(intent(), intent(segments=[web], services=[isp, FW, inside]), Scope(peers=PEERS))["operations"]
    order = [(o["kind"], o["service"]["name"] if "service" in o else o.get("firewall")) for o in ops]
    assert order == [("create_segment", None), ("create_service", "FW"), ("create_service", "AAA"),
                     ("route", None), ("create_service", "IN"), ("route", "IN")]

def test_nothing_to_do_at_the_same_intent():
    now = intent(segments=[WEB], services=[FW], fabric={"snmp": {"location": "a"}})
    assert plan(now, copy.deepcopy(now), Scope(peers=PEERS)) == {"operations": [], "notes": []}


def test_preview_on_the_lab_model(database_url):
    nb = example_netbox()
    segments = Segments(nb)
    run(segments.create("example", "web", 10500, 500))
    run(segments.attach("example", "leaf1", "10GE1/0/3", 10500, None))
    store = IntentSnapshots(db.make_sessionmaker(db.make_engine(database_url, pooled=False)), nb)
    point = run(store.take("example", job_id=1))
    run(segments.detach("example", "leaf1", "10GE1/0/3", 10500))
    run(segments.create("example", "db", 10600, 600))

    whole = run(preview(nb, store, "example", point["id"], None))
    assert [o["kind"] for o in whole["operations"]] == ["delete_segment", "attach"] and whole["scope"] == "fabric"
    switch = run(preview(nb, store, "example", point["id"], "leaf1"))
    assert [o["kind"] for o in switch["operations"]] == ["attach"] and switch["scope"] == ["leaf1", "leaf2"]


# -- applying (step 3b) ---------------------------------------------------------------------------

import pytest  # noqa: E402

from orchestrator.rollback import apply  # noqa: E402
from orchestrator.routing import Routing  # noqa: E402
from orchestrator.segments import SegmentError  # noqa: E402


def example_with_store(database_url):
    nb = example_netbox()
    return nb, IntentSnapshots(db.make_sessionmaker(db.make_engine(database_url, pooled=False)), nb)


def state(nb):
    return [(s["name"], s["vni"], s["routing"]["gateway"] if s["routing"] else None, s["attachments"])
            for s in run(Segments(nb).all("example"))]


def test_restoring_the_fabric_brings_intent_back(database_url):
    nb, store = example_with_store(database_url)
    segments = Segments(nb)
    run(segments.create("example", "web", 10500, 500))
    run(segments.attach("example", "leaf1", "10GE1/0/3", 10500, None))
    point = run(store.take("example", job_id=1))
    before = state(nb)
    run(segments.detach("example", "leaf1", "10GE1/0/3", 10500))
    run(segments.create("example", "db", 10600, 600))

    with pytest.raises(SegmentError, match="preview again"):
        run(apply(nb, store, "example", point["id"], None, expected=99))
    result = run(apply(nb, store, "example", point["id"], None, expected=2))
    assert result["failed"] is None and len(result["done"]) == 2
    assert state(nb) == before
    assert run(preview(nb, store, "example", point["id"], None))["operations"] == []


def test_a_segment_routed_again_gets_its_exact_subnet_back(database_url):
    nb, store = example_with_store(database_url)
    segments, routing = Segments(nb), Routing(nb)
    run(segments.create("example", "web", 10500, 500))
    run(routing.set_routing("example", 10500, True, firewall=None))
    point = run(store.take("example", job_id=1))
    gateway = state(nb)[0][2]
    # Since the point: web unrouted, and its subnet taken by a new segment db.
    run(routing.set_routing("example", 10500, False))
    run(segments.create("example", "db", 10600, 600))
    run(routing.set_routing("example", 10600, True, firewall=None))
    assert [s for s in state(nb) if s[0] == "db"][0][2] == gateway

    result = run(apply(nb, store, "example", point["id"], None,
                       expected=len(run(preview(nb, store, "example", point["id"], None))["operations"])))
    assert result["failed"] is None
    assert state(nb) == [("web", 10500, gateway, [])]   # db went first, so web took its own subnet back


def test_an_exact_subnet_still_taken_is_refused(database_url):
    nb, store = example_with_store(database_url)
    segments, routing = Segments(nb), Routing(nb)
    for name, vni in (("web", 10500), ("db", 10600)):
        run(segments.create("example", name, vni, vni - 10000))
    run(routing.set_routing("example", 10500, True, firewall=None))
    gateway = state(nb)[0][2]                                             # web (VNI 10500) lists first
    run(routing.set_routing("example", 10500, False))
    run(routing.set_routing("example", 10600, True, firewall=None))       # db now holds web's old subnet
    with pytest.raises(SegmentError, match="no longer free"):
        run(routing.set_routing("example", 10500, True, firewall=None, subnet=gateway))
    with pytest.raises(SegmentError, match="outside the fabric's gateway pool"):
        run(routing.set_routing("example", 10500, True, firewall=None, subnet="192.0.2.1/24"))


def test_a_rollback_stops_at_the_first_refusal(database_url):
    nb, store = example_with_store(database_url)
    segments = Segments(nb)
    run(segments.create("example", "web", 10500, 500))
    run(segments.attach("example", "leaf1", "10GE1/0/3", 10500, None))
    point = run(store.take("example", job_id=1))
    run(segments.detach("example", "leaf1", "10GE1/0/3", 10500))
    run(segments.delete("example", 10500))
    run(segments.create("example", "app", 10700, 500))   # takes web's bridge domain; this switch's rollback keeps app
    result = run(apply(nb, store, "example", point["id"], "leaf1", expected=2))
    assert result["done"] == [] and result["failed"]["text"].startswith("create segment web")
    assert result["remaining"] == ["attach web to leaf1 10GE1/0/3 (untagged)"]


def test_a_rollback_never_reaches_the_management_plane():
    """It only runs intent operations, and intent never holds the management plane: routing intent
    refuses `_management_vpn_`, and MEth is never a segment port or trunk member."""
    from orchestrator.routing_intent import DeviceRouting

    intent_ops = {"routing_set", "detach", "set_vid", "attach", "route", "unroute", "delete_service", "service_asn",
                  "create_service", "delete_segment", "rename", "create_segment", "delete_trunk", "create_trunk",
                  "ntp", "snmp", "gateway_mac"}
    vpn_route = {"vpn": "web-500", "prefix": "10.9.0.0/16", "next_hop": "NULL0"}
    scenarios = [
        (intent(segments=[WEB], services=[FW], routing={"devices": {"leaf1+leaf2": {"static_routes": [vpn_route]}}},
                trunks={"leaf1": [{"number": 2, "members": ["10GE1/0/5"], "m_lag": False}]}, fabric={"snmp": {"location": "b"}}),
         intent(services=[{**FW, "vlan": 3001}], fabric={"snmp": {"location": "a"}})),
    ]
    for now, then in scenarios + [(b, a) for a, b in scenarios]:
        for scope in (Scope(peers=PEERS), Scope(devices={"leaf1", "leaf2"}, target="leaf1+leaf2", peers=PEERS)):
            ops = plan(now, then, scope)["operations"]
            assert ops and {o["kind"] for o in ops} <= intent_ops
            assert "MEth" not in repr(ops) and "_management_vpn_" not in repr(ops)
    with pytest.raises(ValueError):
        DeviceRouting.model_validate({"static_routes": [{"vpn": "_management_vpn_", "prefix": "0.0.0.0/0", "next_hop": "172.24.0.1"}]})


def test_dhcp_relay_and_netstream_come_back_with_a_fabric_restore():
    """DHCP relay: GUI-owned intent like the rest; relay after the segment's routing."""
    relay = {"servers": [{"address": "192.0.2.10"}], "loopback": 2}
    routed = {"gateway": "10.100.0.1/24", "peers": [], "firewall": "FW"}
    now = intent(segments=[seg("web", 10500, 500, routed, [("leaf1", "10GE1/0/3", None)])], services=[FW],
                 fabric={"netstream": {"enabled": False, "host": "192.0.2.9", "port": 2055}})
    then = intent(segments=[seg("web", 10500, 500, {**routed, "relay": relay}, [("leaf1", "10GE1/0/3", None)])], services=[FW],
                  fabric={"netstream": {"enabled": True, "host": "192.0.2.9", "port": 2055}})
    result = plan(now, then, Scope(peers=PEERS))
    assert [k for k, _ in kinds(result)] == ["route", "relay", "netstream"]
    assert result["operations"][1]["servers"] == [{"address": "192.0.2.10"}]
    assert result["operations"][2]["text"] == "NetStream on, to 192.0.2.9:2055"
    back = plan(then, now, Scope(peers=PEERS))
    assert [(o["kind"], o.get("servers")) for o in back["operations"] if o["kind"] == "relay"] == [("relay", [])]


def test_microsegmentation_comes_back_whole_with_a_fabric_restore():
    """the document as at the rollback point (IDs and precedences included)."""
    doc = {"mode": "enforce", "epgs": {"1": {"segment": 10500}}, "contracts": []}
    now, then = intent(fabric={}), intent(fabric={"microsegmentation": doc})
    result = plan(now, then, Scope(peers=PEERS))
    assert [(o["kind"], o["document"]) for o in result["operations"]] == [("microseg", doc)]
    assert plan(then, now, Scope(peers=PEERS))["operations"][0]["document"]["mode"] == "off"


def test_dc_links_come_back_with_a_fabric_restore():
    """the fabric's DC links as at the rollback point."""
    link = {"name": "dc", "split_group": "DCI", "sides": [], "segments": []}
    now, then = intent(fabric={}), {**intent(fabric={}), "dci": [link]}
    result = plan({**now, "dci": []}, then, Scope(peers=PEERS))
    assert [(o["kind"], o["links"]) for o in result["operations"]] == [("dci", [link])]
    assert plan(now, intent(fabric={}), Scope(peers=PEERS))["operations"] == []   # an old snapshot: no DCI in it


def test_qos_comes_back_whole_with_a_fabric_restore():
    """QoS on or off with its marks as at the rollback point."""
    doc = {"enabled": True, "segments": {"10500": "storage"}, "ports": {"leaf1": {"10GE1/0/3": "scavenger"}}}
    result = plan(intent(fabric={}), intent(fabric={"qos": doc}), Scope(peers=PEERS))
    assert [(o["kind"], o["document"]) for o in result["operations"]] == [("qos", doc)]
    assert result["operations"][0]["text"] == "QoS on, 1 segment and 1 port mark(s) as at the rollback point"
    assert plan(intent(fabric={"qos": doc}), intent(fabric={}), Scope(peers=PEERS))["operations"][0]["document"]["enabled"] is False


def test_ipv6_comes_back_after_routing_and_before_relay():
    """a fabric restore puts a segment's IPv6 back between its routing and its relay."""
    routed = {"gateway": "10.100.0.1/24", "peers": [], "firewall": None}
    six = {"gateway": "fd00:1::1/64", "hosts": "dhcpv6", "servers": [{"address": "fd00:1::53"}]}
    relay = {"servers": [], "loopback": 2}
    now = intent(segments=[seg("web", 10500, 500, None, [("leaf1", "10GE1/0/3", None)])])
    then = intent(segments=[seg("web", 10500, 500, {**routed, "ipv6": six, "relay": relay}, [("leaf1", "10GE1/0/3", None)])])
    result = plan(now, then, Scope(peers=PEERS))
    assert [k for k, _ in kinds(result)] == ["route", "ipv6", "relay"]
    assert result["operations"][1]["servers"] == [{"address": "fd00:1::53"}]


def test_a_segment_that_goes_leaves_its_firewall_before_the_service_goes():
    """The orchestrator refuses deleting a service that is still a segment's firewall."""
    routed = {"gateway": "10.100.0.1/24", "peers": [], "firewall": "FW"}
    now = intent(segments=[seg("web", 10500, 500, routed)], services=[FW])
    result = plan(now, intent(), Scope(peers=PEERS))
    assert [k for k, _ in kinds(result)] == ["route", "delete_service", "unroute", "delete_segment"]
    assert result["operations"][0]["firewall"] is None
