"""Centralized fabrics in the orchestrator: services and segment VPN instances are on the
gateway pair only."""

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orchestrator.app import app, get_netbox
from orchestrator.routing import Routing
from orchestrator.routing_intent import DeviceRouting, RoutingStore
from orchestrator.segments import SegmentError, Segments
from tests.helpers import seed
from seed.model import load, parse
from tests.seed.fake_netbox import FakeNetBox

CENTRAL = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example-central.yaml"
SITE = "example-central"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, load(CENTRAL)))
    return netbox


def routed_web(nb) -> Segments:
    segments = Segments(nb)
    run(segments.create(SITE, "web", 10500, 500))
    run(segments.attach(SITE, "c-leaf1", "Eth-Trunk1", 10500, 500))
    run(Routing(nb).set_routing(SITE, 10500, True, firewall=None))
    return segments


def test_services_sit_on_the_gateway_pair(nb):
    r = Routing(nb)
    with pytest.raises(SegmentError, match="centralized: services sit on its gateway pair"):
        run(r.create_service(SITE, "FW", "c-leaf1", "10GE1/0/10", 3000, "10.255.0.1/30", "10.255.0.2"))
    service = run(r.create_service(SITE, "FW", "c-gw1", "Eth-Trunk1", 3000, "10.255.0.0/29", "10.255.0.6"))
    assert service["devices"] == ["c-gw1", "c-gw2"]


def test_segment_vpn_instances_are_on_the_gateways_only(nb):
    routed_web(nb)
    store = RoutingStore(nb)

    assert run(store.gateways(SITE)) == {"c-gw1", "c-gw2"}
    assert run(store.vpns(SITE, "c-gw1+c-gw2")) == ["web-500"]
    assert run(store.vpns(SITE, "c-leaf1+c-leaf2")) == []     # attached there, but layer 2 only

    with pytest.raises(SegmentError, match="VPN instance web-500 is not on c-leaf1"):
        run(store.set_device(SITE, "c-leaf1+c-leaf2", DeviceRouting.model_validate(
            {"static_routes": [{"vpn": "web-500", "prefix": "10.9.0.0/16", "next_hop": "NULL0"}]})))


def test_the_guard_follows_the_gateways(nb):
    segments = routed_web(nb)
    store = RoutingStore(nb)
    run(store.set_device(SITE, "c-gw1+c-gw2", DeviceRouting.model_validate(
        {"static_routes": [{"vpn": "web-500", "prefix": "10.9.0.0/16", "next_hop": "NULL0"}]})))

    # Detaching the last port keeps web-500 on the gateways: allowed.
    run(segments.detach(SITE, "c-leaf1", "Eth-Trunk1", 10500))
    with pytest.raises(SegmentError, match="would remove VPN instance web-500 from c-gw1"):
        run(Routing(nb).set_routing(SITE, 10500, False))


def test_the_api_names_the_architecture(nb):
    from tests.collector.test_collector import example_netbox

    app.dependency_overrides[get_netbox] = lambda: nb
    try:
        client = TestClient(app)
        fabric = client.get(f"/fabrics/{SITE}").json()
        fabric.pop("settings")   # read-only settings for the Fabric page, tested in test_fabric_ntp
        fabric.pop("deploys_allowed")   # tested in test_fabric_admin
        assert fabric == {
            "site": SITE, "architecture": "centralized", "gateway_pair": ["c-gw1", "c-gw2"],
            "ntp": {"servers": ["192.0.2.1", "192.0.2.2"], "vpn_instance": None},
            "netstream": {"enabled": False, "host": None, "port": None},
            "syslog": {"enabled": False, "host": None, "port": None}, "relay_pool": None, "ipv6_pool": None}
        app.dependency_overrides[get_netbox] = example_netbox
        assert client.get("/fabrics/example").json()["architecture"] == "symmetric"
    finally:
        app.dependency_overrides.pop(get_netbox, None)


def test_a_4_byte_asn_fabric_keeps_vnis_within_2_bytes():
    """A 4-byte ASN: route target <ASN>:<VNI> leaves 2 bytes for the VNI."""
    netbox = FakeNetBox()
    data = yaml.safe_load((CENTRAL.parent / "example-central-spines.yaml").read_text())
    data["fabric"]["asn"] = 4200000101
    run(seed(netbox, parse(data)))
    segments = Segments(netbox)

    with pytest.raises(SegmentError, match="its VNIs are 1-65535"):
        run(segments.create("example-central-spines", "big", 70000, 700))
    run(segments.create("example-central-spines", "web", 65535, 500))
