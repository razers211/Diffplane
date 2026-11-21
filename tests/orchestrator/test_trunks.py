"""Eth-Trunks from the GUI: checks on create and delete, retirement, the API."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
from orchestrator.routing import Routing
from orchestrator.routing_intent import RoutingStore
from orchestrator.segments import SegmentError, Segments
from orchestrator.trunks import Trunks
from tests.helpers import seed
from seed.model import load
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


def iface(nb, device, name):
    device_id = next(d["id"] for d in nb.objects["dcim/devices"].values() if d["name"] == device)
    return next((i for i in nb.objects["dcim/interfaces"].values() if i["device"] == device_id and i["name"] == name), None)


def test_list_shows_the_seeded_trunks_and_free_ports(nb):
    found = run(Trunks(nb).of_device("leaf1"))
    assert found["peer"] == "leaf2"
    assert [(t["name"], t["kind"], t["members"]) for t in found["trunks"]] == [
        ("Eth-Trunk0", "peer-link", ["100GE1/0/5", "100GE1/0/6"]),
        ("Eth-Trunk1", "m-lag", ["10GE1/0/1", "10GE1/0/2"]),
    ]
    free = found["free_ports"]
    assert "10GE1/0/3" in free and "10GE1/0/1" not in free      # a member is not free
    assert "100GE1/0/1" not in free                             # a fabric link (address) is not free
    assert "MEth0/0/0" not in free
    assert found["pair_free_ports"] == free                     # the planned peer has the same ports free
    assert found["next_number"] == 2


def test_create_single_and_mlag_trunks(nb):
    t = Trunks(nb)
    run(t.create("leaf1", 2, ["10GE1/0/5", "10GE1/0/6"], mlag=False))
    run(t.create("leaf1", 3, ["10GE1/0/7"], mlag=True))

    assert iface(nb, "leaf1", "Eth-Trunk2")["tags"] == []
    assert iface(nb, "leaf2", "Eth-Trunk2") is None
    for leaf in ("leaf1", "leaf2"):
        trunk = iface(nb, leaf, "Eth-Trunk3")
        assert len(trunk["tags"]) == 1
        assert iface(nb, leaf, "10GE1/0/7")["lag"] == trunk["id"]
    kinds = {x["name"]: x["kind"] for x in run(t.of_device("leaf1"))["trunks"]}
    assert kinds == {"Eth-Trunk0": "peer-link", "Eth-Trunk1": "m-lag", "Eth-Trunk2": "single", "Eth-Trunk3": "m-lag"}


def test_create_refusals(nb):
    t = Trunks(nb)
    for args, message in [
        (("leaf1", 1, ["10GE1/0/5"], False), "already has Eth-Trunk1"),
        (("leaf1", 0, ["10GE1/0/5"], False), "1-1023"),
        (("leaf1", 5, [], False), "at least one member"),
        (("leaf1", 5, ["10GE1/0/1"], False), "10GE1/0/1 is not free"),      # in Eth-Trunk1
        (("leaf1", 5, ["100GE1/0/1"], False), "100GE1/0/1 is not free"),    # fabric link
        (("leaf1", 5, ["MEth0/0/0"], False), "MEth0/0/0 is not free"),
        (("leaf1", 5, ["10GE1/0/99"], False), "has no port"),
        (("spine1", 5, ["10GE1/0/5"], True), "not in an M-LAG pair"),
    ]:
        with pytest.raises(SegmentError, match=message):
            run(t.create(*args))


def test_a_port_with_segments_cannot_join_a_trunk(nb):
    run(Segments(nb).create("example", "web", 10500, 500))
    run(Segments(nb).attach("example", "leaf1", "10GE1/0/3", 10500, None))
    with pytest.raises(SegmentError, match="10GE1/0/3 is not free"):
        run(Trunks(nb).create("leaf1", 5, ["10GE1/0/3"], mlag=False))


def test_segments_on_a_single_trunk_stay_on_its_device(nb):
    run(Trunks(nb).create("leaf1", 2, ["10GE1/0/5"], mlag=False))
    run(Trunks(nb).create("leaf2", 2, ["10GE1/0/5"], mlag=False))
    run(Segments(nb).create("example", "web", 10500, 500))
    run(Segments(nb).attach("example", "leaf1", "Eth-Trunk2", 10500, 500))
    assert iface(nb, "leaf1", "Eth-Trunk2")["custom_fields"]["segments"] == [{"vni": 10500, "vid": 500}]
    assert not iface(nb, "leaf2", "Eth-Trunk2")["custom_fields"].get("segments")


def test_a_service_on_a_single_trunk_is_its_devices_alone(nb):
    run(Trunks(nb).create("leaf1", 2, ["10GE1/0/5"], mlag=False))
    service = run(Routing(nb).create_service("example", "FW", "leaf1", "Eth-Trunk2", 3000, "10.255.0.0/29",
                                             "10.255.0.6"))
    assert service["devices"] == ["leaf1"] and service["addresses"] is None
    with pytest.raises(SegmentError, match="M-LAG trunk"):
        run(Routing(nb).create_service("example", "FW2", "leaf1", "Eth-Trunk2", 3001, "10.255.1.1/29",
                                       "10.255.1.6", addresses={"leaf1": "10.255.1.2/29"}))
    with pytest.raises(SegmentError, match="service's port"):
        run(Trunks(nb).delete("leaf1", "Eth-Trunk2"))
    run(Segments(nb).create("example", "web", 10500, 500))
    with pytest.raises(SegmentError):   # dedicated: no segments next to the service
        run(Segments(nb).attach("example", "leaf1", "Eth-Trunk2", 10500, 500))


def test_delete_retires_on_active_devices_only(nb):
    t = Trunks(nb)
    run(t.create("leaf1", 3, ["10GE1/0/7", "10GE1/0/8"], mlag=True))
    assert run(t.delete("leaf2", "Eth-Trunk3")) == {"devices": ["leaf2", "leaf1"], "name": "Eth-Trunk3"}

    assert iface(nb, "leaf1", "Eth-Trunk3") is None and iface(nb, "leaf2", "Eth-Trunk3") is None
    assert iface(nb, "leaf1", "10GE1/0/7")["lag"] is None
    document = run(RoutingStore(nb).load("example"))
    # leaf2 is planned: never deployed, nothing to remove there.
    assert document.retired_trunks == {"leaf1": {"Eth-Trunk3": ["10GE1/0/7", "10GE1/0/8"]}}

    run(RoutingStore(nb).clear_retired("leaf1"))   # a verified deploy
    assert run(RoutingStore(nb).load("example")).retired_trunks == {}


def test_delete_refusals(nb):
    t = Trunks(nb)
    with pytest.raises(SegmentError, match="peer-link"):
        run(t.delete("leaf1", "Eth-Trunk0"))
    with pytest.raises(SegmentError, match="no trunk"):
        run(t.delete("leaf1", "Eth-Trunk7"))
    run(Segments(nb).create("example", "web", 10500, 500))
    run(Segments(nb).attach("example", "leaf1", "Eth-Trunk1", 10500, 500))
    with pytest.raises(SegmentError, match="carries segments"):
        run(t.delete("leaf1", "Eth-Trunk1"))


def test_a_service_trunk_cannot_be_deleted(nb):
    run(Routing(nb).create_service("example", "FW", "leaf1", "Eth-Trunk1", 3000, "10.255.0.0/29", "10.255.0.6"))
    with pytest.raises(SegmentError, match="service's port"):
        run(Trunks(nb).delete("leaf1", "Eth-Trunk1"))


def test_api_create_list_delete_and_audit(nb):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None   # tested in test_intent_freeze.py
    try:
        client = TestClient(app)
        created = client.post("/devices/leaf1/trunks", json={"number": 2, "members": ["10GE1/0/5"]})
        assert created.status_code == 201, created.text
        assert client.post("/devices/leaf1/trunks", json={"number": 2, "members": ["10GE1/0/6"]}).status_code == 400
        assert [t["name"] for t in client.get("/devices/leaf1/trunks").json()["trunks"]] == \
            ["Eth-Trunk0", "Eth-Trunk1", "Eth-Trunk2"]
        assert client.delete("/devices/leaf1/trunks/Eth-Trunk2").status_code == 200
        assert client.get("/devices/nope/trunks").status_code == 404
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["trunk.create", "trunk.delete"]


def test_the_seed_tags_yaml_trunks_and_never_recreates_deleted_ones(nb):
    run(Trunks(nb).delete("leaf1", "Eth-Trunk1"))
    run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))   # a re-seed
    assert iface(nb, "leaf1", "Eth-Trunk1") is None


def test_the_seed_tags_a_trunk_made_before_the_tag(nb):
    trunk = iface(nb, "leaf1", "Eth-Trunk1")
    trunk["tags"] = []
    report = run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    assert len(iface(nb, "leaf1", "Eth-Trunk1")["tags"]) == 1
    assert "update dcim/interfaces leaf1 Eth-Trunk1: tags" in report.changes
