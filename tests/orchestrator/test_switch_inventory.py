"""What a switch is, read from it: model, release, ports with their NetBox types, kept in NetBox."""

import asyncio
import re
from pathlib import Path

import pytest

from orchestrator import switch_inventory as si
from orchestrator.segments import SegmentError
from seed import ports
from tests.collector.test_collector import example_netbox

FIXTURES = Path(__file__).parents[1] / "collector" / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / f"inventory-{name}.xml").read_text()


def found(kind: str) -> si.Found:
    return si.parse(fixture(f"system-{kind}"), fixture(f"ports-{kind}"))


@pytest.fixture(autouse=True)
def forget_learned():
    yield
    ports.LEARNED.clear()


def test_hardware_types_give_the_netbox_type():
    assert si.netbox_type("Eth10GEPort") == "10gbase-t"
    assert si.netbox_type("Eth10GFPort") == "10gbase-x-sfpp"
    assert si.netbox_type("Eth25GFPort") == "25gbase-x-sfp28"
    assert si.netbox_type("Eth100GFPort") == "100gbase-x-qsfp28"
    assert si.netbox_type("Eth400GFPort") == "400gbase-x-qsfpdd"
    assert si.netbox_type("Eth1GEPort") == "1000base-t"
    assert si.netbox_type("Eth3GFPort") == "other" and si.netbox_type("") == "other"


def test_switch_replies_parse_into_model_release_and_ports():
    fibre, copper = found("fibre"), found("copper")
    assert (fibre.model, fibre.version, fibre.patch) == ("CE6881H-48S6CQ", "V300R025C00SPC500", "V300R025SPH120")
    assert copper.model == "CE6881H-48T6CQ" and copper.serial == "2102000000001"
    for f in (fibre, copper):
        assert len(f.ports) == 54 and "MEth0/0/0" not in f.ports
        assert list(f.ports)[:2] == ["10GE1/0/1", "10GE1/0/2"] and list(f.ports)[-1] == "100GE1/0/6"
        assert f.ports["100GE1/0/1"] == "100gbase-x-qsfp28"
    assert fibre.ports["10GE1/0/1"] == "10gbase-x-sfpp" and copper.ports["10GE1/0/1"] == "10gbase-t"


def test_a_reply_without_a_model_is_refused():
    with pytest.raises(SegmentError, match="did not report its model"):
        si.parse('<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>', fixture("ports-fibre"))


def test_learning_a_copper_switch_retypes_its_ports_and_teaches_the_model():
    nb = example_netbox()
    result = asyncio.run(si.learn(nb, "leaf1", found("copper")))
    assert result["changed"] and result["was_model"] == "CE6881H-48S6CQ" and result["model"] == "CE6881H-48T6CQ"
    assert len(result["retyped"]) == 48 and result["added"] == result["removed"] == result["blocked"] == []
    device = asyncio.run(nb.find_one("dcim/devices", name="leaf1"))
    assert device["device_type"]["model"] == "CE6881H-48T6CQ" and device["serial"] == "2102000000001"
    port = asyncio.run(nb.find_one("dcim/interfaces", device_id=device["id"], name="10GE1/0/7"))
    assert ports.is_copper(port["type"]["value"])
    assert ports.layout("CE6881H-48T6CQ")["10GE1/0/7"] == "10gbase-t"   # learned: the model's templates
    assert not asyncio.run(si.learn(nb, "leaf1", found("copper")))["changed"]   # nothing left to do


def test_a_model_with_other_port_names_replaces_free_ports():
    nb = example_netbox()
    xml = re.sub(r"<position>10GE1/0/(\d+)</position>", r"<position>25GE1/0/\1</position>", fixture("ports-fibre"))
    xml = xml.replace("Eth10GFPort", "Eth25GFPort")
    other = si.parse(fixture("system-fibre").replace("CE6881H-48S6CQ", "CE6863H-48S6CQ"), xml)
    result = asyncio.run(si.learn(nb, "leaf1", other))
    # the M-LAG trunk's members 10GE1/0/1-2 are in use: nothing changes on the device
    assert not result["changed"] and result["blocked"] == ["10GE1/0/1", "10GE1/0/2"]
    assert "not changed" in si.summary(result)
    device = asyncio.run(nb.find_one("dcim/devices", name="leaf1"))
    assert device["device_type"]["model"] == "CE6881H-48S6CQ"
    assert ports.ports_of("CE6863H-48S6CQ")[0] == "25GE1/0/1"   # the model's layout is still learned

    for name in ("10GE1/0/1", "10GE1/0/2"):   # once those are free, the switch's ports replace NetBox's
        iface = asyncio.run(nb.find_one("dcim/interfaces", device_id=device["id"], name=name))
        asyncio.run(nb.update("dcim/interfaces", iface["id"], {"lag": None}))
    result = asyncio.run(si.learn(nb, "leaf1", other))
    assert result["changed"] and len(result["removed"]) == 48 and len(result["added"]) == 48
    names = {i["name"] for i in asyncio.run(nb.find("dcim/interfaces", device_id=device["id"]))}
    assert "25GE1/0/48" in names and "10GE1/0/48" not in names and "Eth-Trunk1" in names


def test_the_sweep_reads_active_pinned_switches_only_and_never_during_a_rollout(monkeypatch):
    import orchestrator.auth as auth
    import orchestrator.deploys as deploys

    nb = example_netbox()
    leaf = asyncio.run(nb.find_one("dcim/devices", name="leaf1"))   # pinned for NETCONF, as after approval
    asyncio.run(nb.update("dcim/devices", leaf["id"], {"custom_fields": {**leaf["custom_fields"], "mlag_peer": (
        leaf["custom_fields"].get("mlag_peer") or {}).get("id"), "ssh_host_keys": {"830": ["ssh-rsa AAAA"]}}}))
    audited = []

    class Reader:
        def __init__(self):
            self.asked = []

        async def get_state(self, name, subtrees):
            self.asked.append(name)
            return [fixture("system-fibre"), fixture("ports-fibre")]

    async def rollout(_):
        return 7

    async def none(_):
        return None

    async def record(sessions, user, action, job_id=None, detail=None):
        audited.append((user, action, detail))

    monkeypatch.setattr(auth, "audit", record)
    sweep = si.Sweep(sessions=None, netbox=nb, interval=0)
    monkeypatch.setattr(deploys, "rolling_out", rollout)
    assert asyncio.run(sweep.run_once(Reader())) == []
    monkeypatch.setattr(deploys, "rolling_out", none)
    reader = Reader()
    results = asyncio.run(sweep.run_once(reader))
    pinned = ["leaf1"]
    assert reader.asked == pinned and [r["device"] for r in results] == pinned
    assert all(a[1] == "switch.inventory" for a in audited)


def test_a_model_without_digits_takes_the_product_name_and_one_annet_would_not_commit_on_is_refused():
    """An eNSP CE reports hardware-model "CE": Annet then sent no commit (preview, 2026-11-20)."""
    def system(model, product):
        return (f'<data><system xmlns="{si.SYSTEM_NS}"><system-info><hardware-model>{model}</hardware-model>'
                f'<product-name>{product}</product-name></system-info></system></data>')
    assert si.parse(system("CE", "CE6800"), fixture("ports-fibre")).model == "CE6800"
    with pytest.raises(SegmentError, match="not one Annet commits on"):
        si.parse(system("CE", ""), fixture("ports-fibre"))
