"""Intent files: export, then import back as checked intent operations."""

import asyncio

import pytest
import yaml

from orchestrator import intent_file
from orchestrator.segments import SegmentError, Segments
from tests.collector.test_collector import example_netbox


def run(coro):
    return asyncio.run(coro)


def fabric_with_a_segment():
    nb = example_netbox()
    segments = Segments(nb)
    run(segments.create("example", "web", 10500, 500))
    run(segments.attach("example", "leaf1", "10GE1/0/3", 10500, None))
    return nb


def test_an_exported_file_imports_as_nothing_to_do():
    nb = fabric_with_a_segment()
    text = run(intent_file.export(nb, "example"))
    assert text.startswith("kind: diffplane-intent\nversion: 1\nfabric: example\n")
    assert "&" not in text and "*" not in text   # no YAML anchors or aliases
    result = run(intent_file.preview(nb, "example", text))
    assert result["operations"] == [] and result["ignored"] == []


def test_a_segment_added_in_the_file_is_created_and_attached():
    nb = fabric_with_a_segment()
    data = yaml.safe_load(run(intent_file.export(nb, "example")))
    data["segments"].append({"name": "db", "vni": 10600, "bd_id": 600, "routing": None,
                             "attachments": [{"device": "leaf1", "interface": "10GE1/0/5", "vid": 600}]})
    text = yaml.safe_dump(data)
    planned = run(intent_file.preview(nb, "example", text))
    assert [o["kind"] for o in planned["operations"]] == ["create_segment", "attach"]
    with pytest.raises(SegmentError, match="preview again"):
        run(intent_file.apply(nb, "example", text, expected=5))
    result = run(intent_file.apply(nb, "example", text, expected=2))
    assert result["failed"] is None
    assert run(intent_file.preview(nb, "example", text))["operations"] == []


def test_the_orchestrators_checks_still_apply():
    nb = fabric_with_a_segment()
    data = yaml.safe_load(run(intent_file.export(nb, "example")))
    data["segments"].append({"name": "db\nsnmp-agent", "vni": 10600, "bd_id": 600, "routing": None, "attachments": []})
    text = yaml.safe_dump(data)
    result = run(intent_file.apply(nb, "example", text, expected=1))
    assert result["done"] == [] and result["failed"]["text"].startswith("create segment")


def test_what_a_file_may_not_change_is_kept_and_said():
    nb = fabric_with_a_segment()
    data = yaml.safe_load(run(intent_file.export(nb, "example")))
    data["settings"]["qos"] = {"enabled": True, "segments": {}, "ports": {}}
    data["dci"] = [{"name": "other", "sides": []}]
    data["settings"]["snmp"] = {"location": "hall 2", "contact": "noc", "readers": {"x": {"auth": "md5", "priv": "des56"}}}
    result = run(intent_file.preview(nb, "example", yaml.safe_dump(data)))
    assert sorted(result["ignored"]) == ["DC links", "QoS", "SNMPv3 readers and trap targets"]
    assert [o["kind"] for o in result["operations"]] == ["snmp"]
    assert result["operations"][0]["readers"] == {}   # today's readers, not the file's


@pytest.mark.parametrize("text, error", [
    ("kind: diffplane-intent\nversion: 1\nfabric: other\n", "fabric 'other'"),
    ("kind: something\n", "not an intent file"),
    ("kind: diffplane-intent\nversion: 2\nfabric: example\n", "version 2"),
    ("kind: diffplane-intent\nversion: 1\nfabric: example\nsegments: [&a {name: x}, *a]\n", "aliases"),
    ("kind: diffplane-intent\nversion: 1\nfabric: example\nsegments: [{name: x, vni: '1', bd_id: 1}]\n", "segments.0.vni"),
    ("kind: diffplane-intent\nversion: 1\nfabric: example\nsegments: [{name: x, vni: 1, bd_id: 1, extra: 1}]\n", "extra"),
    ("kind: diffplane-intent\nversion: 1\nfabric: example\nservices: !!python/object:object {}\n", "not YAML"),
    ("[unclosed", "not YAML"),
])
def test_a_file_is_checked_before_anything(text, error):
    with pytest.raises(SegmentError, match=error):
        intent_file.parse(text, "example")


def test_a_routed_fabric_round_trips_and_a_removal_is_planned_in_order():
    from orchestrator.routing import Routing
    from orchestrator.routing_intent import DeviceRouting, RoutingStore

    nb = fabric_with_a_segment()
    routing = Routing(nb)
    run(routing.create_service("example", "FW", device="leaf1", port="10GE1/0/10", vlan=3001,
                               address="10.255.0.1/29", next_hop="10.255.0.2", asn=64700))
    run(routing.set_routing("example", 10500, True, firewall="FW"))
    run(RoutingStore(nb).set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(
        {"prefix_lists": {"OWN": [{"index": 10, "action": "permit", "prefix": "198.51.100.0/24"}]}})))
    text = run(intent_file.export(nb, "example"))
    assert run(intent_file.preview(nb, "example", text))["operations"] == []

    data = yaml.safe_load(text)
    data["segments"] = []
    data["services"] = []
    data["routing"]["devices"] = {}
    planned = run(intent_file.preview(nb, "example", yaml.safe_dump(data)))
    assert [o["text"] for o in planned["operations"]] == [
        "detach web from leaf1 10GE1/0/3", "segment web: no firewall for now (its service is replaced)",
        "delete service FW", "segment web: not routed (frees 10.100.0.1/24)", "delete segment web (VNI 10500)",
        "routing of every switch and the fabric's lists and policies as in the file"]
    result = run(intent_file.apply(nb, "example", yaml.safe_dump(data), expected=6))
    assert result["failed"] is None
    assert run(intent_file.preview(nb, "example", yaml.safe_dump(data)))["operations"] == []
