"""Pre-deploy checks: errors block approval, warnings inform."""

import asyncio
import ipaddress

from orchestrator.checks import ERROR, WARNING, Model, check_fabric, run
from orchestrator.routing import Routing
from orchestrator.segments import Segments
from tests.collector.test_collector import example_netbox

FW = {"name": "FW", "device": "leaf1", "port": "10GE1/0/4", "vlan": 3000, "address": "10.255.0.1/30",
      "next_hop": "10.255.0.2", "asn": 65100, "kind": "firewall", "addresses": None, "devices": ["leaf1"]}


def seg(name, vni, gateway=None, firewall=None, peers=(), attachments=(("leaf1", "10GE1/0/3"),)):
    routing = {"gateway": gateway, "firewall": firewall, "peers": list(peers)} if gateway else None
    return {"name": name, "vni": vni, "bd_id": vni - 10000, "routing": routing,
            "attachments": [{"device": d, "interface": i, "vid": None} for d, i in attachments]}


def model(**changes) -> Model:
    base = dict(segments=[seg("web", 10500, "10.100.0.1/24", "FW"), seg("db", 10600, "10.100.1.1/24", peers=[10500])],
                services=[FW], routing={}, pools={"loopbacks": ipaddress.ip_network("10.10.0.0/24")},
                planned=set(), ports_down=set(), mlag_trunks=set(), peers={"leaf1": "leaf2", "leaf2": "leaf1"})
    return Model(**{**base, **changes})


def checks(found):
    return [(f["severity"], f["check"]) for f in found]


def test_a_sound_fabric_has_no_finding():
    assert run(model()) == []


def test_a_static_route_loop_is_an_error():
    statics = [{"vpn": "web-500", "prefix": "10.9.0.0/16", "next_hop": "10.8.0.1"},
               {"vpn": "web-500", "prefix": "10.8.0.0/16", "next_hop": "10.9.0.1"}]
    found = run(model(routing={"devices": {"leaf1+leaf2": {"static_routes": statics}}}))
    assert checks(found) == [(ERROR, "route-cycle")]
    assert "on leaf1+leaf2" in found[0]["text"] and "static 10.9.0.0/16 via 10.8.0.1 in web-500" in found[0]["text"]
    # The same routes on two different switches do not meet: no loop.
    split = {"leaf1": {"static_routes": statics[:1]}, "leaf3": {"static_routes": statics[1:]}}
    assert run(model(routing={"devices": split})) == []


def test_overlapping_subnets_and_pools_are_errors():
    found = run(model(services=[{**FW, "address": "10.100.0.129/25"}]))
    assert (ERROR, "duplicate-address") in checks(found)
    found = run(model(segments=[seg("web", 10500, "10.10.0.1/24", None)]))
    assert checks(found) == [(ERROR, "pool-overlap")] and "loopbacks pool" in found[0]["text"]


def test_warnings():
    found = run(model(segments=[seg("web", 10500, "10.100.0.1/24", "FW", attachments=())], planned={"leaf1"}))
    assert checks(found) == [(WARNING, "service-planned"), (WARNING, "unattached")]
    assert checks(run(model(ports_down={("leaf1", "10GE1/0/4")}))) == [(WARNING, "service-down")]
    isp = {**FW, "name": "ISP-A", "kind": "isp", "asn": None}
    peer = {"vpn": "ISP-A", "address": "203.0.113.1", "asn": 64999}
    found = run(model(services=[FW, isp], routing={"devices": {"leaf1": {"bgp_peers": [peer]}}}))
    assert (WARNING, "isp-export") in checks(found)
    with_policy = {"devices": {"leaf1": {"bgp_peers": [{**peer, "export_policy": "OUT"}]}}}
    assert (WARNING, "isp-export") not in checks(run(model(services=[FW, isp], routing=with_policy)))
    one_side = [seg("web", 10500, attachments=(("leaf1", "Eth-Trunk1"),))]
    found = run(model(segments=one_side, mlag_trunks={("leaf1", "Eth-Trunk1"), ("leaf2", "Eth-Trunk1")}))
    assert checks(found) == [(WARNING, "one-sided-mlag")]


def test_on_the_lab_model():
    nb = example_netbox()
    run_ = asyncio.run
    run_(Segments(nb).create("example", "web", 10500, 500))
    run_(Routing(nb).set_routing("example", 10500, True, firewall=None))
    result = run_(check_fabric(nb, None, "example"))
    assert result["blocking"] is False and result["link_state"] == "unknown"
    assert [f["check"] for f in result["findings"]] == ["unattached"]


def routed(name, gateway, firewall=None, peers=()):
    return {"name": name, "bd_id": 1, "routing": {"gateway": gateway, "firewall": firewall, "peers": list(peers)}}


ROUTED = {10500: routed("web", "10.100.0.1/24", "FW"), 10600: routed("db", "10.100.1.1/24", peers=[10500]),
          10700: routed("app", "10.100.2.1/24", "FW")}


def test_microsegmentation_checks():
    """limits block approval; in Enforce, segments that reach each other without a contract,
    contracts with an inactive end and groups left empty are warnings."""
    epgs = {"1": {"segment": 10500}, "2": {"segment": 10600}, "3": {"segment": 10700},
            "4": {"segment": 10500, "name": "admins", "prefixes": ["10.200.0.16/28"], "macs": []},   # stale
            "5": {"segment": 10800}}                                                                   # not routed
    doc = {"mode": "enforce", "epgs": epgs, "contracts": [{"from": 1, "to": 2, "services": [{"protocol": "any"}], "precedence": 1},
                                                         {"from": 5, "to": 1, "services": [{"protocol": "any"}], "precedence": 2}]}
    found = run(model(microseg=doc, routed=ROUTED))
    assert [(f["check"], f["objects"]) for f in found if f["check"].startswith("microseg")] == [
        ("microseg-empty", ["web-admins"]),
        ("microseg-inactive", []),
        ("microseg-no-contract", ["web", "app"]),   # both behind FW; web and db have a contract
    ]
    learn = checks(run(model(microseg={**doc, "mode": "learn"}, routed=ROUTED)))
    assert ("warning", "microseg-empty") in learn and ("warning", "microseg-no-contract") not in learn   # Learn drops nothing
    assert not [f for f in run(model(microseg={**doc, "mode": "off"}, routed=ROUTED)) if f["check"].startswith("microseg")]


def test_microsegmentation_over_the_limits_blocks(monkeypatch):
    from orchestrator import microseg

    monkeypatch.setitem(microseg.LIMITS, "epgs", 1)
    doc = {"mode": "learn", "epgs": {"1": {"segment": 10500}, "2": {"segment": 10600}}, "contracts": []}
    found = run(model(microseg=doc, routed=ROUTED))
    assert ("error", "microseg-limit") in checks(found)


def test_an_unverified_port_layout_blocks_until_the_switch_is_read_and_matches():
    """CE6881H-48T6CQ-K's port names are assumed."""
    from orchestrator.checks import ERROR, port_layouts

    ours = {f"10GE1/0/{n}" for n in range(1, 49)} | {f"100GE1/0/{n}" for n in range(1, 7)}
    unread = port_layouts(model(layouts={"dc2-leaf1": ("CE6881H-48T6CQ-K", ours, None, None)}))
    assert [(f["severity"], f["check"]) for f in unread] == [(ERROR, "port-layout")] and "not read" in unread[0]["text"]
    assert port_layouts(model(layouts={"dc2-leaf1": ("CE6881H-48T6CQ-K", ours, set(ours), None)})) == []
    other = {n.replace("10GE", "MultiGE") for n in ours}
    text = port_layouts(model(layouts={"dc2-leaf1": ("CE6881H-48T6CQ-K", ours, other, None)}))[0]["text"]
    assert "not on the switch: 10GE1/0/1, 10GE1/0/2" in text and "not in the model: MultiGE1/0/1" in text


def test_every_active_switch_must_report_netbox_s_model_and_ports():
    """Any model, read or bootstrap; a model read from a switch is not blocked before the collector reads it."""
    from orchestrator.checks import port_layouts

    ours = {"25GE1/0/1", "100GE1/0/1"}
    wrong = port_layouts(model(layouts={"s1": ("CE6881H-48S6CQ", ours, set(ours), "CE8850-64CQ-EI")}))
    assert len(wrong) == 1 and "reports model CE8850-64CQ-EI, NetBox has CE6881H-48S6CQ" in wrong[0]["text"]
    assert port_layouts(model(layouts={"s1": ("CE8850-64CQ-EI", ours, None, None)})) == []
    differs = port_layouts(model(layouts={"s1": ("CE8850-64CQ-EI", ours, {"25GE1/0/1"}, "CE8850-64CQ-EI")}))
    assert "not on the switch: 100GE1/0/1" in differs[0]["text"]
