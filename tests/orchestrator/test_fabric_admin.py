"""Fabric administration from the GUI: SNMP and gateway MAC, deploys allowed per fabric,
deleting a fabric."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, fabric_delete, fabric_ntp
from orchestrator.app import app, current_account, current_user, get_accounts, get_netbox, get_settings
from orchestrator.auth import Accounts
from orchestrator.platform_settings import PlatformSettings
from orchestrator.segments import SegmentError, Segments
from seed.model import load
from tests.collector.test_collector import example_netbox
from tests.helpers import seed
from tests.orchestrator.test_segments import second_dc
from tests.seed.test_loader import EXAMPLE


def run(coro):
    return asyncio.run(coro)


def fabric_context(nb, site="example") -> dict:
    return run(nb.find_one("extras/config-contexts", name=f"fabric-{site}"))["data"]["fabric"]


def test_snmp_and_gateway_mac_are_set_and_kept_by_the_seed():
    nb = example_netbox()
    run(fabric_ntp.set_snmp(nb, "example", "hall 2, rack 7", "noc@example.net"))
    assert run(fabric_ntp.set_gateway_mac(nb, "example", "00:00:5E:00:01:02")) == "0000-5e00-0102"
    run(seed(nb, load(EXAMPLE)))   # re-seeding keeps what the GUI set (as for NTP)
    fabric = fabric_context(nb)
    assert fabric["snmp"] == {"location": "hall 2, rack 7", "contact": "noc@example.net"}
    assert fabric["gateway_mac"] == "0000-5e00-0102"
    run(fabric_ntp.set_snmp(nb, "example", "", "noc@example.net"))
    assert fabric_context(nb)["snmp"] == {"contact": "noc@example.net"}
    for bad in ("0100-5e00-0001", "12345", "zzzz-zzzz-zzzz"):   # multicast, short, not hex
        with pytest.raises(SegmentError):
            run(fabric_ntp.set_gateway_mac(nb, "example", bad))
    with pytest.raises(SegmentError, match="one line"):
        run(fabric_ntp.set_snmp(nb, "example", "two\nlines", ""))
    for bad in ("tab\there", "caf\u00e9", "help?"):   # control characters, non-ASCII, VRP's help key
        with pytest.raises(SegmentError):
            run(fabric_ntp.set_snmp(nb, "example", bad, ""))


def test_a_fabric_is_deleted_only_when_all_planned_and_named():
    nb = example_netbox()
    run(seed(nb, second_dc()))
    run(Segments(nb).create("example", "web", 10500, 500))
    leaf = run(nb.find_one("dcim/devices", name="leaf1"))
    run(nb.update("dcim/devices", leaf["id"], {"status": "active"}))

    plan = run(fabric_delete.plan(nb, "example"))
    assert plan["blocker"] and "leaf1" in plan["blocker"] and plan["yaml"] == "example.yaml"
    assert plan["devices"] == ["leaf1", "leaf2", "spine1"] and plan["segments"] == 1
    with pytest.raises(SegmentError, match="decommission first"):
        run(fabric_delete.delete(nb, "example", "example"))

    run(nb.update("dcim/devices", leaf["id"], {"status": "planned"}))
    with pytest.raises(SegmentError, match="type the fabric's name"):
        run(fabric_delete.delete(nb, "example", "dc2"))
    run(fabric_delete.delete(nb, "example", "example"))

    assert run(nb.find_one("dcim/sites", slug="example")) is None
    assert not [d for d in run(nb.find("dcim/devices")) if d["name"].startswith("example-")]
    assert run(nb.find_one("ipam/vrfs", name="fabric-example")) is None
    assert run(nb.find_one("extras/config-contexts", name="fabric-example")) is None
    assert run(nb.find("vpn/l2vpns")) == []
    # the other fabric is untouched
    assert run(nb.find_one("dcim/sites", slug="dc2")) is not None
    assert len([d for d in run(nb.find("dcim/devices")) if d["name"].startswith("dc2-")]) == 3
    assert run(nb.find_one("extras/config-contexts", name="fabric-dc2")) is not None


def test_deploys_are_allowed_per_fabric_by_admins(database_url, monkeypatch):
    monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "example")
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    accounts, settings, nb = Accounts(sessions), PlatformSettings(sessions), example_netbox()
    run(accounts.create_user("root", "root-password", "admin"))
    run(accounts.create_user("bob", "bob-password", "approver"))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides.update({get_accounts: lambda: accounts, get_settings: lambda: settings, get_netbox: lambda: nb})
    try:
        client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        client.post("/auth/login", json={"username": "bob", "password": "bob-password"})
        assert client.put("/fabrics/example/deploys", json={"allowed": False}).status_code == 403
        client.post("/auth/login", json={"username": "root", "password": "root-password"})
        assert client.put("/fabrics/example/deploys", json={"allowed": False}).status_code == 200
        assert client.get("/fabrics/example").json()["deploys_allowed"] is False
        assert client.put("/fabrics/example/deploys", json={"allowed": True, "confirm": "nope"}).status_code == 400
        assert client.put("/fabrics/example/deploys", json={"allowed": True, "confirm": "example"}).status_code == 200
        assert client.get("/fabrics/example").json()["deploys_allowed"] is True
    finally:
        for dep in (get_accounts, get_settings, get_netbox):
            app.dependency_overrides.pop(dep, None)


def test_a_usual_window_is_set_by_approvers(database_url, monkeypatch):
    monkeypatch.setenv("CHANGE_WINDOWS", "{}")   # PlatformSettings writes into os.environ: restored after
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    accounts, settings, nb = Accounts(sessions), PlatformSettings(sessions), example_netbox()
    run(accounts.create_user("olga", "olga-password", "operator"))
    run(accounts.create_user("bob", "bob-password", "approver"))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides.update({get_accounts: lambda: accounts, get_settings: lambda: settings, get_netbox: lambda: nb})
    usual = {"days": ["sat", "tue"], "start": "02:00", "minutes": 180, "zone": "Europe/Paris"}
    try:
        client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        client.post("/auth/login", json={"username": "olga", "password": "olga-password"})
        assert client.put("/fabrics/example/usual-window", json=usual).status_code == 403
        assert client.get("/fabrics/example/usual-window").json() == {"usual": None, "next": None, "upcoming": []}
        client.post("/auth/login", json={"username": "bob", "password": "bob-password"})
        assert client.put("/fabrics/example/usual-window", json={**usual, "zone": "Mars/Base"}).status_code == 400
        shown = client.put("/fabrics/example/usual-window", json=usual).json()
        assert shown["usual"]["days"] == ["tue", "sat"] and shown["next"]["start"].endswith("+00:00")
        assert 2 <= len(shown["upcoming"]) <= 3 and shown["upcoming"][0] == shown["next"]
        assert client.put("/fabrics/example/usual-window").json()["usual"] is None   # no body: cleared
    finally:
        for dep in (get_accounts, get_settings, get_netbox):
            app.dependency_overrides.pop(dep, None)
