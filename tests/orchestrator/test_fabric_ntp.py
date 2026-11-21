"""NTP servers set from the GUI: the seed writes them only for a new
fabric and keeps them afterwards; no servers means the platform manages no NTP."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator import fabric_ntp
from orchestrator.app import app, get_netbox
from orchestrator.segments import SegmentError
from tests.helpers import seed
from seed.model import load
from tests.collector.test_collector import example_netbox
from tests.seed.test_loader import EXAMPLE


def context_ntp(nb) -> dict:
    return asyncio.run(fabric_ntp.get_ntp(nb, "example"))


def test_the_seed_writes_ntp_for_a_new_fabric_and_keeps_the_guis_afterwards():
    nb = example_netbox()
    assert context_ntp(nb) == {"servers": ["203.0.113.123", "203.0.113.124"], "vpn_instance": "_management_vpn_"}

    asyncio.run(fabric_ntp.set_ntp(nb, "example", ["192.0.2.123"], None))
    asyncio.run(seed(nb, load(EXAMPLE)))

    assert context_ntp(nb) == {"servers": ["192.0.2.123"], "vpn_instance": None}


def test_no_servers_is_allowed():
    nb = example_netbox()
    assert asyncio.run(fabric_ntp.set_ntp(nb, "example", [], None)) == {"servers": [], "vpn_instance": None}
    asyncio.run(seed(nb, load(EXAMPLE)))
    assert context_ntp(nb)["servers"] == []


@pytest.mark.parametrize("servers, vpn, message", [
    (["time.example.com"], None, "NTP server"),
    (["192.0.2.1", "192.0.2.1"], None, "listed twice"),
    (["192.0.2.1"], "bad name", "not a VPN instance name"),
])
def test_invalid_ntp_is_refused(servers, vpn, message):
    with pytest.raises(SegmentError, match=message):
        asyncio.run(fabric_ntp.set_ntp(example_netbox(), "example", servers, vpn))


def test_api_sets_ntp(monkeypatch):
    from orchestrator import app as app_module

    async def unfrozen():
        return None

    nb = example_netbox()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[app_module._intent_unfrozen] = unfrozen
    app.dependency_overrides[app_module.get_accounts] = lambda: type("A", (), {"sessions": None})()
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app)
        response = client.put("/fabrics/example/ntp", json={"servers": ["192.0.2.7"], "vpn_instance": "_management_vpn_"})
        assert response.status_code == 200, response.text
        fabric = client.get("/fabrics/example").json()
        assert fabric["ntp"] == {"servers": ["192.0.2.7"], "vpn_instance": "_management_vpn_"}
        assert fabric["settings"] == {   # seed/fabrics/example.yaml, read only on the Fabric page
            "asn": 65001, "bypass_vlan": 4093, "gateway_mac": "0000-5e00-0101",
            "snmp_location": "example-dc", "snmp_contact": "noc@example.com",
            "pools": {"loopback": "10.10.0.0/24", "vtep": "10.10.1.0/24", "p2p": "10.11.0.0/24",
                      "mlag_bypass": "10.12.0.0/24", "gateways": "10.100.0.0/16"}}
        assert client.put("/fabrics/example/ntp", json={"servers": ["nope"]}).status_code == 400
        assert client.get("/fabrics/nope").status_code == 404
    finally:
        for dep in (get_netbox, app_module._intent_unfrozen, app_module.get_accounts):
            app.dependency_overrides.pop(dep, None)


def test_api_lists_the_fabrics():
    from seed.loader import Seeder as S
    from tests.orchestrator.test_centralized import CENTRAL

    nb = example_netbox()
    asyncio.run(S(nb, load(CENTRAL)).run())
    app.dependency_overrides[get_netbox] = lambda: nb
    try:
        fabrics = TestClient(app).get("/fabrics").json()
    finally:
        app.dependency_overrides.pop(get_netbox, None)
    assert fabrics == [{"site": "example", "name": "example", "architecture": "symmetric"},
                       {"site": "example-central", "name": "example-central", "architecture": "centralized"}]


def test_netstream_is_off_until_turned_on_keeps_its_collector_and_survives_a_reseed():
    """per fabric from the Fabric page; off keeps the address for next time."""
    nb = example_netbox()
    assert asyncio.run(fabric_ntp.get_netstream(nb, "example")) == {"enabled": False, "host": None, "port": None}
    assert asyncio.run(fabric_ntp.set_netstream(nb, "example", True, "198.51.100.2", None)) == {
        "enabled": True, "host": "198.51.100.2", "port": 2055}
    asyncio.run(seed(nb, load(EXAMPLE)))
    assert asyncio.run(fabric_ntp.get_netstream(nb, "example"))["enabled"] is True
    assert asyncio.run(fabric_ntp.set_netstream(nb, "example", False, None, None)) == {
        "enabled": False, "host": "198.51.100.2", "port": 2055}


@pytest.mark.parametrize("host, port, message", [
    (None, None, "give the collector's address"),
    ("collector.example.com", None, "collector address"),
    ("224.0.0.9", None, "unicast"),
    ("192.0.2.9", 70000, "1 to 65535"),
])
def test_invalid_netstream_is_refused(host, port, message):
    with pytest.raises(SegmentError, match=message):
        asyncio.run(fabric_ntp.set_netstream(example_netbox(), "example", True, host, port))


def test_syslog_is_off_until_turned_on_and_remembers_every_address_it_wrote():
    """per fabric from the Fabric page; off keeps the address; every receiver the platform
    ever wrote stays owned, so its loghost line goes when syslog moves or goes off."""
    nb = example_netbox()
    assert asyncio.run(fabric_ntp.get_syslog(nb, "example")) == {"enabled": False, "host": None, "port": None, "owned": []}
    assert asyncio.run(fabric_ntp.set_syslog(nb, "example", True, "172.24.10.60", None)) == {
        "enabled": True, "host": "172.24.10.60", "port": 514, "owned": ["172.24.10.60"]}
    asyncio.run(fabric_ntp.set_syslog(nb, "example", True, "172.24.10.52", 5514))
    asyncio.run(seed(nb, load(EXAMPLE)))
    assert asyncio.run(fabric_ntp.set_syslog(nb, "example", False, None, None)) == {
        "enabled": False, "host": "172.24.10.52", "port": 5514, "owned": ["172.24.10.52", "172.24.10.60"]}


@pytest.mark.parametrize("host, port, message", [
    (None, None, "give the receiver's address"),
    ("syslog.example.com", None, "receiver address"),
    ("224.0.0.9", None, "unicast"),
    ("192.0.2.9", 70000, "1 to 65535"),
])
def test_invalid_syslog_is_refused(host, port, message):
    with pytest.raises(SegmentError, match=message):
        asyncio.run(fabric_ntp.set_syslog(example_netbox(), "example", True, host, port))



def test_the_fabric_api_turns_syslog_on_without_showing_what_it_owns(monkeypatch):
    from orchestrator import app as app_module

    async def unfrozen():
        return None

    nb = example_netbox()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[app_module._intent_unfrozen] = unfrozen
    app.dependency_overrides[app_module.get_accounts] = lambda: type("A", (), {"sessions": None})()
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app)
        response = client.put("/fabrics/example/syslog", json={"enabled": True, "host": "172.24.10.52"})
        assert response.json() == {"enabled": True, "host": "172.24.10.52", "port": 514}, response.text
        assert client.get("/fabrics/example").json()["syslog"] == {"enabled": True, "host": "172.24.10.52", "port": 514}
        assert client.put("/fabrics/example/syslog", json={"enabled": True, "host": "nope"}).status_code == 400
    finally:
        for dep in (get_netbox, app_module._intent_unfrozen, app_module.get_accounts):
            app.dependency_overrides.pop(dep, None)
