"""Import sessions over the API: addresses -> keys approved -> read -> preview -> write.
The switch is leaf1 as the SSH probe read it (tests/orchestrator/data/import)."""

import asyncio
import base64
import struct
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orchestrator import hostkeys, imports as imp, overrides
from orchestrator.app import _intent_unfrozen, app, current_account, get_accounts, get_imports, get_netbox
from orchestrator.hostkeys import Offer
from orchestrator.import_preview import preview
from tests.orchestrator.test_fabric_import import example_outputs
from tests.orchestrator.test_segments import FakeAccounts
from tests.seed.fake_netbox import FakeNetBox

ADDRESS = "172.24.0.11/24"
CHOICES = {"name": "example", "pools": {"gateways": "10.100.0.0/16"}}


def public_key(kind: str = "ssh-rsa", fill: bytes = b"\x00") -> str:
    """A key whose blob names its type, as hostkeys.parse_key checks; not a real key."""
    blob = struct.pack(">I", len(kind)) + kind.encode() + fill * 16
    return f"{kind} {base64.b64encode(blob).decode()}"


class Switch:
    """Offers one host key on both ports and answers the import's display commands."""

    def __init__(self):
        self.key = public_key()
        self.reads = []
        self.netconf = True

    def fetch(self, host, port, timeout=10.0):
        if port == hostkeys.NETCONF_PORT and not self.netconf:
            raise hostkeys.HostKeyError("cannot read the host key algorithms")
        return Offer([self.key])

    def read(self, host, pins, login, password, commands):
        self.reads.append((host, tuple(pins[22]), login, tuple(commands)))
        out = example_outputs()
        return "leaf1", [out["version"], out["lldp"], out["config"]]


async def in_process(request):
    return await asyncio.to_thread(preview, request)   # preview runs its own event loop, as in its process


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "test-community")   # the example fixture's alias
    switch = Switch()
    netbox, accounts = FakeNetBox(), FakeAccounts()
    imports = imp.Imports(fetch=switch.fetch, read=switch.read, run_preview=in_process,
                          env={"COLLECTOR_LOGIN": "reader", "COLLECTOR_PASSWORD": "secret-pw"})
    app.dependency_overrides.update({get_netbox: lambda: netbox, get_accounts: lambda: accounts,
                                     get_imports: lambda: imports, _intent_unfrozen: lambda: None})
    try:
        yield TestClient(app), switch, netbox, accounts
    finally:
        for dep in (get_netbox, get_accounts, get_imports, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)


def test_import_flow(client):
    http, switch, netbox, accounts = client
    started = http.post("/imports", json={"addresses": [ADDRESS]})
    assert started.status_code == 201, started.text
    session = started.json()["id"]
    (offered,) = started.json()["switches"]
    assert offered["offered"]["22"][0]["sha256"] == hostkeys.fingerprint(switch.key) and not offered["approved"]

    # nothing is read before an approver trusted the keys
    assert http.post(f"/imports/{session}/preview", json=CHOICES).status_code == 400
    http.post(f"/imports/{session}/read")
    assert switch.reads == []

    approved = http.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]})
    assert approved.status_code == 200, approved.text
    read = http.post(f"/imports/{session}/read").json()
    assert read["switches"][0]["sysname"] == "leaf1" and read["switches"][0]["read"]
    assert switch.reads == [("172.24.0.11", (switch.key,), "reader", tuple(imp.COMMANDS))]

    shown = http.post(f"/imports/{session}/preview", json=CHOICES)
    assert shown.status_code == 200, shown.text
    assert shown.json()["patches"] == {"leaf1": ""}
    assert shown.json()["entries"] == overrides.entries(shown.json()["plan"]["overrides"])
    for response in (started, approved, shown):   # the running config never leaves the orchestrator
        assert "authentication-mode" not in response.text and "PLACEHOLDER" not in response.text

    written = http.post(f"/imports/{session}/write", json=CHOICES)
    assert written.status_code == 201, written.text
    device = asyncio.run(netbox.find_one("dcim/devices", name="leaf1"))
    assert device["status"]["value"] == "active" and device["primary_ip4"]["address"] == ADDRESS
    assert device["custom_fields"][hostkeys.FIELD] == {"22": [switch.key], "830": [switch.key]}
    planned = asyncio.run(netbox.find_one("dcim/devices", name="leaf2"))
    assert planned["status"]["value"] == "planned" and not planned["custom_fields"].get(hostkeys.FIELD)
    assert http.get(f"/imports/{session}").status_code == 400   # the session ended
    assert [a for _, a, _ in accounts.events] == ["import.approve-keys", "fabric.import"]


def test_keys_must_be_exactly_what_the_switch_offers(client):
    http, switch, _, _ = client
    session = http.post("/imports", json={"addresses": [ADDRESS]}).json()["id"]
    other = public_key("ssh-ed25519", b"\x01")
    refused = http.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [other]})
    assert refused.status_code == 400 and "did not give" in refused.json()["detail"]


def test_a_switch_with_netconf_off_is_imported_with_its_ssh_key_alone(client):
    http, switch, netbox, _ = client
    switch.netconf = False
    started = http.post("/imports", json={"addresses": [ADDRESS]}).json()
    session = started["id"]
    assert list(started["switches"][0]["offered"]) == ["22"] and not started["switches"][0].get("error")
    approved = http.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]})
    assert approved.status_code == 200, approved.text
    http.post(f"/imports/{session}/read")
    assert http.post(f"/imports/{session}/write", json=CHOICES).status_code == 201
    device = asyncio.run(netbox.find_one("dcim/devices", name="leaf1"))
    assert device["custom_fields"][hostkeys.FIELD] == {"22": [switch.key]}


def test_addresses_are_checked(client):
    http, *_ = client
    assert "with its mask" in http.post("/imports", json={"addresses": ["172.24.0.11"]}).json()["detail"]
    assert "twice" in http.post("/imports", json={"addresses": [ADDRESS, "172.24.0.11/25"]}).json()["detail"]


def test_approving_needs_an_approver(client):
    http, switch, _, _ = client
    session = http.post("/imports", json={"addresses": [ADDRESS]}).json()["id"]
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="op", role="operator")
    try:
        assert http.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]}).status_code == 403
    finally:
        app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="tester", role="approver")


def test_a_switch_already_in_netbox_is_previewed_never_written(client):
    http, switch, netbox, _ = client
    device = asyncio.run(netbox.create("dcim/devices", {"name": "leaf1"}))
    asyncio.run(netbox.create("ipam/ip-addresses", {"address": ADDRESS, "assigned_object_type": "dcim.interface",
                                                    "assigned_object_id": device["id"]}))
    started = http.post("/imports", json={"addresses": [ADDRESS]}).json()
    assert started["switches"][0]["in_netbox"]
    session = started["id"]
    http.post(f"/imports/{session}/approve", json={"address": ADDRESS, "keys": [switch.key]})
    http.post(f"/imports/{session}/read")
    assert http.post(f"/imports/{session}/preview", json=CHOICES).json()["patches"] == {"leaf1": ""}
    refused = http.post(f"/imports/{session}/write", json=CHOICES)
    assert refused.status_code == 400 and "preview only" in refused.json()["detail"]
