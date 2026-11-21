"""Where each switch is: locations, racks, U positions, from the GUI."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.app import app, get_accounts, get_netbox
from orchestrator.locations import Locations
from orchestrator.segments import SegmentError
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


def test_racks_and_placement(nb):
    loc = Locations(nb)
    run(loc.add_rack("example", "Hall A", "R01"))
    run(loc.add_rack("example", "Hall A", "R02", 47))
    assert run(loc.place("leaf1", "Hall A", "R01", 40, "front"))["position"] == 40
    run(loc.place("leaf2", "Hall A", "R01", 39, "rear"))
    run(loc.place("spine1", "Hall A", "R02", None, None))   # in the rack, no U yet

    found = run(loc.racks("example"))
    (hall,) = found["locations"]
    assert hall["name"] == "Hall A"
    r01, r02 = hall["racks"]
    assert (r01["name"], r01["u_height"], r02["u_height"]) == ("R01", 42, 47)
    assert [(d["name"], d["position"], d["face"]) for d in r01["devices"]] == [
        ("leaf1", 40, "front"), ("leaf2", 39, "rear")]
    assert r02["devices"] == [{"name": "spine1", "position": None, "face": None, "u_height": 1}]


def test_placement_refusals(nb):
    loc = Locations(nb)
    run(loc.add_rack("example", "Hall A", "R01", 42))
    run(loc.place("leaf1", "Hall A", "R01", 40, "front"))
    for args, message in [
        (("leaf2", "Hall A", "R01", 40, "front"), "taken by leaf1"),
        (("leaf2", "Hall A", "R01", 43, "front"), "1-42"),
        (("leaf2", "Hall A", "R01", 0, "front"), "1-42"),
        (("leaf2", "Hall A", "R01", 10, None), "front or rear"),
        (("leaf2", "Hall A", "R09", 10, "front"), "no rack"),
        (("nope", "Hall A", "R01", 10, "front"), "no device"),
    ]:
        with pytest.raises(SegmentError, match=message):
            run(loc.place(*args))
    with pytest.raises(SegmentError, match="already has rack"):
        run(loc.add_rack("example", "Hall A", "R01"))
    with pytest.raises(SegmentError, match="1-60 U"):
        run(loc.add_rack("example", "Hall A", "R03", 0))
    with pytest.raises(SegmentError, match="name"):
        run(loc.add_rack("example", "", "R03"))


def test_a_rack_is_deleted_only_when_empty_and_takes_its_last_location(nb):
    loc = Locations(nb)
    run(loc.add_rack("example", "Hall A", "R01"))
    run(loc.place("leaf1", "Hall A", "R01", 40, "front"))
    with pytest.raises(SegmentError, match="holds leaf1"):
        run(loc.delete_rack("example", "Hall A", "R01"))
    run(loc.place("leaf1", None, None, None, None))   # out of the rack
    run(loc.delete_rack("example", "Hall A", "R01"))
    assert run(loc.racks("example"))["locations"] == []


def test_api_places_a_device_and_the_device_list_shows_it(nb):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    try:
        client = TestClient(app)
        assert client.post("/fabrics/example/racks", json={"location": "Hall A", "rack": "R01"}).status_code == 201
        response = client.put("/devices/leaf1/location",
                              json={"location": "Hall A", "rack": "R01", "position": 40, "face": "front"})
        assert response.status_code == 200, response.text
        assert client.put("/devices/leaf2/location", json={"location": "Hall A", "rack": "R01", "position": 40,
                                                               "face": "front"}).status_code == 400
        leaf = next(d for d in client.get("/devices", params={"site": "example"}).json() if d["name"] == "leaf1")
        assert (leaf["location"], leaf["rack"], leaf["position"], leaf["face"]) == ("Hall A", "R01", 40, "front")
        assert client.get("/fabrics/example/racks").json()["locations"][0]["racks"][0]["devices"][0]["name"] == "leaf1"
        assert client.get("/fabrics/nope/racks").status_code == 404
    finally:
        app.dependency_overrides.pop(get_netbox, None)
        app.dependency_overrides.pop(get_accounts, None)
    assert [a for _, a, _ in accounts.events] == ["rack.create", "device.location"]
