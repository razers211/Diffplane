import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
from orchestrator.segments import SegmentError, Segments, subif_number
from tests.helpers import seed
from seed.model import load
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]


def second_dc():
    from seed.model import parse
    from tests.helpers import dc2_data

    return parse(dc2_data())


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    asyncio.run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    asyncio.run(seed(netbox, second_dc()))
    return netbox


def example_site(nb) -> int:
    return next(site["id"] for site in nb.objects["dcim/sites"].values() if site["slug"] == "example")


def run(coro):
    return asyncio.run(coro)


def port_segments(nb, device, interface):
    device_id = next(d["id"] for d in nb.objects["dcim/devices"].values() if d["name"] == device)
    iface = next(i for i in nb.objects["dcim/interfaces"].values() if i["device"] == device_id and i["name"] == interface)
    return (iface.get("custom_fields") or {}).get("segments")


def context(nb, fabric="example"):
    return next(c for c in nb.objects["extras/config-contexts"].values() if c["name"] == f"segments-{fabric}")["data"]


def test_create_segment_and_publish_it_for_generators(nb):
    s = Segments(nb)

    run(s.create("example", "web", 10500, 500))

    (l2vpn,) = nb.objects["vpn/l2vpns"].values()
    assert (l2vpn["type"], l2vpn["identifier"], l2vpn["custom_fields"]["bd_id"]) == ("vxlan-evpn", 10500, 500)
    assert l2vpn["name"] == "example:web" and l2vpn["custom_fields"]["fabric"] == example_site(nb)
    assert context(nb) == {"segments": {"10500": {"bd_id": 500, "name": "web"}}, "services": {}}
    assert run(s.next_bd_id("example")) == 501


@pytest.mark.parametrize(
    "name, vni, bd, message",
    [("web", 10500, 600, "VNI 10500"), ("db", 10600, 500, "bridge-domain ID 500"), ("web", 10700, 700, "named"),
     ("x", 0, 1, "VNI must be"), ("x", 1, 16777216, "bridge-domain ID must be"), (" ", 1, 1, "name"),
     ("x", 15777216, 1, "VNI must be"), ("a:b", 1, 1, "only letters, digits"),
     ("web tier", 1, 1, "only letters, digits"), ("a" * 28, 1, 500, "too long: its VPN instance")],
)
def test_create_refuses_duplicates_and_bad_ids(nb, name, vni, bd, message):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))

    with pytest.raises(SegmentError, match=message):
        run(s.create("example", name, vni, bd))


def test_tagged_and_untagged_on_a_physical_port(nb):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))
    run(s.create("example", "db", 10600, 600))

    run(s.attach("example", "leaf1", "10GE1/0/3", 10500, 500))
    run(s.attach("example", "leaf1", "10GE1/0/3", 10600, None))

    assert port_segments(nb, "leaf1", "10GE1/0/3") == [{"vni": 10500, "vid": 500}, {"vni": 10600, "vid": None}]
    listed = {seg["vni"]: seg["attachments"] for seg in run(s.all("example"))}
    assert listed[10600] == [{"device": "leaf1", "interface": "10GE1/0/3", "vid": None}]


def test_attaching_to_an_mlag_trunk_writes_both_leaves(nb):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))

    ports = run(s.attach("example", "leaf1", "Eth-Trunk1", 10500, 500))

    assert ports == ["leaf1 Eth-Trunk1", "leaf2 Eth-Trunk1"]
    assert port_segments(nb, "leaf2", "Eth-Trunk1") == [{"vni": 10500, "vid": 500}]

    run(s.detach("example", "leaf2", "Eth-Trunk1", 10500))

    assert not port_segments(nb, "leaf1", "Eth-Trunk1") and not port_segments(nb, "leaf2", "Eth-Trunk1")


@pytest.mark.parametrize(
    "device, interface, message",
    [("leaf1", "100GE1/0/1", "routed fabric link"), ("leaf1", "Eth-Trunk0", "peer-link"),
     ("leaf1", "100GE1/0/5", "trunk member"), ("leaf1", "10GE1/0/1", "trunk member"),
     ("leaf1", "MEth0/0/0", "only physical ports"), ("leaf1", "LoopBack0", "only physical ports"),
     ("spine1", "100GE1/0/9", "not a leaf"), ("leaf1", "10GE1/0/99", "no interface"),
     ("nope", "10GE1/0/3", "no device"), ("dc2-leaf1", "10GE1/0/3", "not in fabric example")],
)
def test_ports_that_cannot_carry_segments(nb, device, interface, message):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))

    with pytest.raises(SegmentError, match=message):
        run(s.attach("example", device, interface, 10500, 500))


def test_port_rules(nb):
    s = Segments(nb)
    run(s.create("example", "a", 10001, 1))
    run(s.create("example", "b", 10002, 2))
    run(s.create("example", "c", 10003, 3))
    run(s.attach("example", "leaf1", "10GE1/0/3", 10002, None))

    with pytest.raises(SegmentError, match="only one untagged"):
        run(s.attach("example", "leaf1", "10GE1/0/3", 10003, None))
    with pytest.raises(SegmentError, match="collide"):  # tagged BD 1 -> .1, same as untagged
        run(s.attach("example", "leaf1", "10GE1/0/3", 10001, 100))
    run(s.attach("example", "leaf1", "10GE1/0/3", 10003, 300))
    with pytest.raises(SegmentError, match="already carries"):
        run(s.attach("example", "leaf1", "10GE1/0/3", 10003, 301))
    with pytest.raises(SegmentError, match="VLAN ID must be"):
        run(s.attach("example", "leaf1", "10GE1/0/4", 10003, 4095))


def test_change_vid_and_tagged_to_untagged(nb):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))
    run(s.attach("example", "leaf1", "10GE1/0/3", 10500, 500))

    run(s.set_vid("example", "leaf1", "10GE1/0/3", 10500, 501))
    assert port_segments(nb, "leaf1", "10GE1/0/3") == [{"vni": 10500, "vid": 501}]
    run(s.set_vid("example", "leaf1", "10GE1/0/3", 10500, None))
    assert port_segments(nb, "leaf1", "10GE1/0/3") == [{"vni": 10500, "vid": None}]


def test_delete_needs_detached_segment(nb):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))
    run(s.attach("example", "leaf1", "10GE1/0/3", 10500, None))

    with pytest.raises(SegmentError, match="detach it first"):
        run(s.delete("example", 10500))
    run(s.detach("example", "leaf1", "10GE1/0/3", 10500))
    run(s.delete("example", 10500))

    assert not nb.objects["vpn/l2vpns"] and context(nb) == {"segments": {}, "services": {}}


def test_subif_numbering():
    assert subif_number(500, 500) == 500 and subif_number(500, 42) == 500 and subif_number(500, None) == 1


# -- HTTP ----------------------------------------------------------------------------

class FakeAccounts:
    def __init__(self):
        self.events = []
        accounts = self

        class _Sessions:
            def __call__(self):
                return _Session()

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def add(self, event):
                accounts.events.append((event.username, event.action, event.detail))

            async def commit(self):
                pass

        self.sessions = _Sessions()


def test_http_segment_flow_is_audited(nb):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None   # tested in test_intent_freeze.py
    client = TestClient(app)

    assert client.get("/segments/next-bd-id", params={"site": "example"}).json() == {"bd_id": 1}
    assert client.post("/segments", json={"site": "example", "name": "web", "vni": 10500, "bd_id": 500}).status_code == 201
    response = client.post("/segments/example/10500/attachments", json={"device": "leaf1", "interface": "Eth-Trunk1",
                                                                    "vid": 500})
    assert response.status_code == 201 and response.json() == {"ports": ["leaf1 Eth-Trunk1", "leaf2 Eth-Trunk1"]}
    assert client.post("/segments/example/10500/attachments",
                       json={"device": "leaf1", "interface": "100GE1/0/1"}).status_code == 400
    assert client.get("/segments", params={"site": "example"}).json()[0]["attachments"][0]["vid"] == 500
    assert client.get("/segments", params={"site": "dc2"}).json() == []
    assert client.delete("/segments/example/10500/attachments", params={"device": "leaf1",
                                                                   "interface": "Eth-Trunk1"}).status_code == 200

    assert [a for _, a, _ in accounts.events] == ["segment.create", "segment.attach", "segment.detach"]
    assert all(user == "tester" for user, _, _ in accounts.events)
    for dep in (get_netbox, get_accounts, _intent_unfrozen):
        app.dependency_overrides.pop(dep, None)


def test_attachable_ports_follow_the_attach_rules(nb):
    ports = run(Segments(nb).ports("leaf1"))
    names = [p["name"] for p in ports]

    assert names[0] == "Eth-Trunk1" and ports[0]["trunk"] is True
    assert "10GE1/0/3" in names and names.index("10GE1/0/3") < names.index("10GE1/0/10")
    for refused in ("Eth-Trunk0", "100GE1/0/1", "100GE1/0/5", "10GE1/0/1", "MEth0/0/0", "LoopBack0"):
        assert refused not in names


def test_attachable_ports_use_a_fixed_number_of_netbox_requests(nb):
    calls = []
    original = nb.find

    async def counting(endpoint, **filters):
        calls.append(endpoint)
        return await original(endpoint, **filters)

    nb.find = counting
    run(Segments(nb).ports("leaf1"))

    assert len(calls) <= 5, calls   # device, interfaces, addresses, services, overrides (VLAN trunks)


# -- multi-DC ------------------------------------------------------------------------

def test_two_fabrics_can_reuse_names_vnis_and_bridge_domains(nb):
    s = Segments(nb)

    run(s.create("example", "web", 10500, 500))
    run(s.create("dc2", "web", 10500, 500))
    run(s.attach("dc2", "dc2-leaf1", "10GE1/0/3", 10500, None))

    assert {l["name"] for l in nb.objects["vpn/l2vpns"].values()} == {"example:web", "dc2:web"}
    assert context(nb, "example") == context(nb, "dc2") == {"segments": {"10500": {"bd_id": 500, "name": "web"}},
                                                        "services": {}}
    example_web = run(s.all("example"))[0]
    assert example_web["attachments"] == []   # dc2's attachment is not example's


def test_each_fabric_context_is_assigned_to_its_site_only(nb):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))

    ctx = next(c for c in nb.objects["extras/config-contexts"].values() if c["name"] == "segments-example")

    assert ctx["sites"] == [example_site(nb)]


def test_the_old_global_context_is_removed(nb):
    run(nb.create("extras/config-contexts", {"name": "segments", "data": {"segments": {}}}))

    run(Segments(nb).create("example", "web", 10500, 500))

    assert not any(c["name"] == "segments" for c in nb.objects["extras/config-contexts"].values())


def test_legacy_segments_are_adopted_or_listed_as_unassigned(nb):
    s = Segments(nb)
    run(nb.create("vpn/l2vpns", {"name": "web", "slug": "web", "type": "vxlan-evpn", "identifier": 10500,
                                 "custom_fields": {"bd_id": 500}}))
    run(nb.create("vpn/l2vpns", {"name": "old", "slug": "old", "type": "vxlan-evpn", "identifier": 10900,
                                 "custom_fields": {"bd_id": 900}}))
    port = next(i for i in nb.objects["dcim/interfaces"].values()
                if i["name"] == "10GE1/0/3" and i["device"] == next(
                    d["id"] for d in nb.objects["dcim/devices"].values() if d["name"] == "leaf1"))
    port.setdefault("custom_fields", {})["segments"] = [{"vni": 10500, "vid": None}]

    listed = run(s.all("example"))

    web = next(x for x in listed if x["vni"] == 10500)
    old = next(x for x in listed if x["vni"] == 10900)
    assert web["fabric"] == "example" and web["attachments"]
    assert old["fabric"] is None
    run(s.delete("unassigned", 10900))
    assert not any(l["identifier"] == 10900 for l in nb.objects["vpn/l2vpns"].values())
