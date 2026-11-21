"""Activate and decommission a switch from the GUI."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import collector.__main__ as collector_main
from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
from orchestrator.lifecycle import Lifecycle
from orchestrator.segments import SegmentError
from seed.model import load
from tests.helpers import seed
from tests.orchestrator.test_segments import FakeAccounts
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    return netbox


def device(nb, name):
    return next(d for d in nb.objects["dcim/devices"].values() if d["name"] == name)


def mgmt_ips(nb, name):
    dev = device(nb, name)
    ifaces = {i["id"] for i in nb.objects["dcim/interfaces"].values() if i["device"] == dev["id"] and i["name"].startswith("MEth")}
    return sorted(ip["address"] for ip in nb.objects["ipam/ip-addresses"].values() if ip.get("assigned_object_id") in ifaces)


def test_activate_a_planned_device(nb):
    result = run(Lifecycle(nb).activate("leaf2", "172.24.0.105/24"))
    assert result == {"device": "leaf2", "status": "active", "interface": "MEth0/0/0",
                      "address": "172.24.0.105/24", "pinned": False}
    leaf2 = device(nb, "leaf2")
    assert leaf2["status"] == "active" and mgmt_ips(nb, "leaf2") == ["172.24.0.105/24"]
    ip = nb.objects["ipam/ip-addresses"][leaf2["primary_ip4"]]
    assert ip["address"] == "172.24.0.105/24" and ip.get("vrf") is None   # global table

    run(Lifecycle(nb).activate("leaf2", "172.24.0.106/24"))           # a new address
    assert mgmt_ips(nb, "leaf2") == ["172.24.0.106/24"]


def test_activation_refusals(nb):
    life = Lifecycle(nb)
    for args, message in [
        (("leaf2", "172.24.0.11/24"), "already the management address of leaf1"),
        (("leaf2", "172.24.0.11/16"), "already the management address of leaf1"),   # any mask
        (("leaf2", "172.24.0.105"), "with its mask"),
        (("leaf2", "172.24.0.0/24"), "with its mask"),
        (("leaf2", "nonsense/24"), "invalid address"),
        (("leaf2", "172.24.0.105/24", "10GE1/0/1"), "MEth port"),
        (("nope", "172.24.0.105/24"), "no device"),
    ]:
        with pytest.raises(SegmentError, match=message):
            run(life.activate(*args))


def test_decommission_frees_the_address_and_forgets_the_pins(nb):
    leaf1 = device(nb, "leaf1")
    leaf1["custom_fields"]["ssh_host_keys"] = {"22": ["ssh-rsa AAAA"], "830": ["ssh-rsa AAAA"]}
    result = run(Lifecycle(nb).decommission("leaf1"))

    assert result == {"device": "leaf1", "status": "planned", "freed": ["172.24.0.11/24"]}
    leaf1 = device(nb, "leaf1")
    assert leaf1["status"] == "planned" and leaf1["primary_ip4"] is None
    assert leaf1["custom_fields"]["ssh_host_keys"] is None and mgmt_ips(nb, "leaf1") == []
    # Its fabric addressing stays.
    assert any(ip.get("role") == "loopback" for ip in nb.objects["ipam/ip-addresses"].values())
    # The collector, drift and deploys only see active devices with a primary IPv4.
    assert run(collector_main.active_devices(nb)) == {}
    with pytest.raises(SegmentError, match="not active"):
        run(Lifecycle(nb).decommission("leaf1"))

    run(Lifecycle(nb).activate("leaf1", "172.24.0.11/24"))           # added back
    assert device(nb, "leaf1")["status"] == "active"


def test_api_activate_and_decommission_are_audited_intent_writes(nb):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None   # tested in test_intent_freeze.py
    try:
        client = TestClient(app)
        response = client.put("/devices/leaf2/management", json={"address": "172.24.0.105/24"})
        assert response.status_code == 200, response.text
        assert client.put("/devices/leaf2/management", json={"address": "172.24.0.11/24"}).status_code == 400
        assert client.delete("/devices/leaf2/management").status_code == 200
        assert client.delete("/devices/leaf2/management").status_code == 400
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["device.activate", "device.decommission"]
