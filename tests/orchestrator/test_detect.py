"""New fabric and Add switches with a detected switch: keys approved, model and ports read from it,
then built with those ports whatever the model."""

import re

import pytest
from fastapi.testclient import TestClient

from orchestrator import hostkeys, imports as imp, switch_inventory as si
from orchestrator.app import _intent_unfrozen, app, get_accounts, get_imports, get_netbox
from seed import ports
from tests.orchestrator.test_fabric_build import device, nb  # noqa: F401  (fixture)
from tests.orchestrator.test_imports import Switch
from tests.orchestrator.test_segments import FakeAccounts
from tests.orchestrator.test_switch_inventory import fixture

ADDRESS = "172.24.0.130/24"
MODEL = "CE6863H-48S6CQ"   # known neither from the start nor learned: only detection gives its ports


def other_model() -> si.Found:
    xml = re.sub(r"<position>10GE1/0/(\d+)</position>", r"<position>25GE1/0/\1</position>", fixture("ports-fibre"))
    return si.parse(fixture("system-fibre").replace("CE6881H-48S6CQ", MODEL), xml.replace("Eth10GFPort", "Eth25GFPort"))


@pytest.fixture
def http(nb):  # noqa: F811
    switch, accounts = Switch(), FakeAccounts()
    reads = []

    def inventory(host, keys, login, password):
        reads.append((host, tuple(keys), login))
        return other_model()

    imports = imp.Imports(fetch=switch.fetch, inventory=inventory,
                          env={"COLLECTOR_LOGIN": "reader", "COLLECTOR_PASSWORD": "secret-pw"})
    app.dependency_overrides.update({get_netbox: lambda: nb, get_accounts: lambda: accounts,
                                     get_imports: lambda: imports, _intent_unfrozen: lambda: None})
    try:
        yield TestClient(app), switch, reads, imports
    finally:
        for dep in (get_netbox, get_accounts, get_imports, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
        ports.LEARNED.clear()


def leaf(model=MODEL):
    return {"leaves": [{"name": "leaf9", "model": model, "role": "leaf", "management": ADDRESS}],
            "links": [{"spine": "spine1", "spine_port": "100GE1/0/5", "leaf": "leaf9", "leaf_port": "100GE1/0/1"}]}


def test_a_detected_switch_of_an_unknown_model_is_built_with_its_own_ports(http, nb):  # noqa: F811
    client, switch, reads, imports = http
    body = leaf()
    refused = client.post("/fabrics/example/switches", json=body)
    assert refused.status_code == 400 and "no known port layout" in refused.text   # not detected: refused

    session = client.post("/imports", json={"addresses": [ADDRESS]}).json()["id"]
    assert client.post(f"/imports/{session}/detect").json()["switches"][0]["detected"] is None
    assert reads == []   # nothing read before an approver trusted the keys
    client.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]})
    detected = client.post(f"/imports/{session}/detect").json()["switches"][0]["detected"]
    assert reads == [("172.24.0.130", (switch.key,), "reader")]
    assert detected["model"] == MODEL and list(detected["ports"])[:1] == ["25GE1/0/1"]

    added = client.post("/fabrics/example/switches", json={**body, "detect": session})
    assert added.status_code == 201, added.text
    leaf9 = device(nb, "leaf9")
    assert leaf9["status"] == "active" and leaf9["serial"] == other_model().serial
    assert leaf9["custom_fields"][hostkeys.FIELD] == {"22": [switch.key], "830": [switch.key]}
    names = {i["name"] for i in nb.objects["dcim/interfaces"].values() if i["device"] == leaf9["id"]}
    assert "25GE1/0/48" in names and "10GE1/0/1" not in names and "100GE1/0/1" in names
    assert ports.ports_of(MODEL)[0] == "25GE1/0/1"   # the model is known from now on
    assert session not in imports.sessions        # the detect session ended with the write


def test_a_detected_switch_must_be_the_model_given(http, nb):  # noqa: F811
    client, switch, _, _ = http
    session = client.post("/imports", json={"addresses": [ADDRESS]}).json()["id"]
    client.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]})
    client.post(f"/imports/{session}/detect")
    body = leaf(model="CE6881H-48S6CQ")
    refused = client.post("/fabrics/example/switches", json={**body, "detect": session})
    assert refused.status_code == 400 and f"reports model {MODEL}" in refused.text
    assert device(nb, "leaf9") is None


def test_netconf_off_is_reported_not_guessed(http):
    client, switch, reads, _ = http
    switch.netconf = False
    session = client.post("/imports", json={"addresses": [ADDRESS]}).json()["id"]
    client.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]})
    view = client.post(f"/imports/{session}/detect").json()["switches"][0]
    assert view["detected"] is None and "NETCONF" in view["error"]

