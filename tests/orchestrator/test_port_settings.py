"""Port settings: off by default; turning them on adopts what the switches' running configs hold
on server ports (never fabric links, trunk members, the peer-link or MEth); then name, on/off and frame
size per port, an M-LAG trunk on both switches; a port turned off that carries something warns."""

import asyncio

import pytest

from orchestrator import checks, port_settings, rollback
from orchestrator.segments import SegmentError
from tests.orchestrator.test_dhcp_relay import example_with_web

RUNNING = """\
#
interface 100GE1/0/1
 undo portswitch
 description to spine1 100GE1/0/1
#
interface 10GE1/0/1
 description member, left alone
 eth-trunk 1
#
interface 10GE1/0/3
 description db-01 (rack A3)
#
interface Eth-Trunk1
 shutdown
 jumboframe enable 9216
 mode lacp-static
#
interface MEth0/0/0
 description management
#
"""


def run(coro):
    return asyncio.run(coro)


class Annet:
    def __init__(self, texts):
        self.texts = texts

    async def current(self, device):
        if device not in self.texts:
            raise RuntimeError("unreachable")
        return self.texts[device]


def pinned(nb, name="leaf1"):
    device = run(nb.find_one("dcim/devices", name=name))
    fields = {k: (v["id"] if k == "mlag_peer" and v else v) for k, v in device["custom_fields"].items()}   # as stored
    run(nb.update("dcim/devices", device["id"], {"custom_fields": {**fields, "ssh_host_keys": {"22": ["k"]}}}))
    return nb


def iface(nb, device, name):
    d = run(nb.find_one("dcim/devices", name=device))
    return port_settings.settings_of(run(nb.find_one("dcim/interfaces", device_id=d["id"], name=name)))


def test_off_by_default_and_nothing_is_set_until_on():
    nb = example_with_web(pool=False)
    assert run(port_settings.document(nb, "example")) == {"enabled": False}
    with pytest.raises(SegmentError, match="not managed"):
        run(port_settings.set_port(nb, "leaf1", "10GE1/0/3", description="x"))


def test_turning_on_adopts_server_ports_only():
    nb = pinned(example_with_web(pool=False))
    found = run(port_settings.adoption(nb, Annet({"leaf1": RUNNING}), "example"))
    assert found["switches"] == ["leaf1"]
    assert {(c["device"], c["port"]): c["switch"] for c in found["changes"]} == {
        ("leaf1", "10GE1/0/3"): {"description": "db-01 (rack A3)", "enabled": True, "frame": None, "speed": None},
        ("leaf1", "Eth-Trunk1"): {"description": "", "enabled": False, "frame": 9216, "speed": None},
    }
    result = run(port_settings.set_enabled(nb, Annet({"leaf1": RUNNING}), "example", True))
    assert result == {"enabled": True, "adopted": ["leaf1 10GE1/0/3", "leaf1 Eth-Trunk1"]}
    assert iface(nb, "leaf1", "Eth-Trunk1") == {"description": "", "enabled": False, "frame": 9216, "speed": None}
    assert iface(nb, "leaf1", "100GE1/0/1")["description"] == ""   # a fabric link: not adopted


def test_nothing_is_adopted_when_a_switch_cannot_be_read():
    nb = pinned(example_with_web(pool=False))
    with pytest.raises(SegmentError, match="could not read leaf1"):
        run(port_settings.set_enabled(nb, Annet({}), "example", True))
    assert run(port_settings.document(nb, "example")) == {"enabled": False}


def test_settings_and_an_mlag_trunk_on_both_switches():
    nb = pinned(example_with_web(pool=False))
    run(port_settings.set_enabled(nb, Annet({"leaf1": ""}), "example", True))
    result = run(port_settings.set_port(nb, "leaf1", "Eth-Trunk1", enabled=False, frame=9000))
    assert result["switches"] == ["leaf1", "leaf2"]
    assert iface(nb, "leaf2", "Eth-Trunk1") == {"description": "", "enabled": False, "frame": 9000, "speed": None}
    off = run(port_settings.set_port(nb, "leaf1", "10GE1/0/3", description=" web-01 ", enabled=False))
    assert off["carries"] == ["segment web"] and off["description"] == "web-01"
    shown = run(port_settings.get_port(nb, "leaf1", "10GE1/0/3"))
    assert (shown["refusal"], shown["managed"], shown["enabled"]) == (None, True, False)
    found = run(checks.check_fabric(nb, None, "example"))["findings"]
    assert any(f["check"] == "port-off-in-use" and "web stop there" in f["text"] for f in found)


@pytest.mark.parametrize("port, given, reason", [
    ("10GE1/0/1", {"description": "x"}, "trunk member: set it on its trunk"),
    ("100GE1/0/1", {"enabled": False}, "fabric link"),
    ("Eth-Trunk0", {"enabled": False}, "peer-link"),
    ("MEth0/0/0", {"description": "x"}, "only front-panel ports"),
    ("10GE1/0/3", {"description": "what?"}, "no '\\?'"),
    ("10GE1/0/3", {"description": "x" * 243}, "at most 242"),
    ("10GE1/0/3", {"frame": 1500}, "1518-15360"),
    ("10GE1/0/3", {"speed": 1000}, "not a copper port"),   # leaf1's ports are SFP+
    ("Eth-Trunk1", {"speed": 1000}, "not a copper port"),
])
def test_refusals(port, given, reason):
    nb = pinned(example_with_web(pool=False))
    run(port_settings.set_enabled(nb, Annet({"leaf1": ""}), "example", True))
    with pytest.raises(SegmentError, match=reason):
        run(port_settings.set_port(nb, "leaf1", port, **given))


def test_rollback_puts_port_settings_back():
    default = {"description": "", "enabled": True, "frame": None, "speed": None}
    now = {"ports": {"leaf1": {"10GE1/0/3": {**default, "enabled": False}}}, "fabric": {"port_profiles": {"enabled": True}}}
    then = {"ports": {"leaf1": {"10GE1/0/5": {**default, "description": "srv"}}}, "fabric": {}}
    ops = rollback.plan(now, then, rollback.Scope(devices=None))["operations"]
    assert [(o["kind"], o.get("port"), o.get("values"), o.get("enabled")) for o in ops] == [
        ("port", "10GE1/0/3", default, None), ("port", "10GE1/0/5", {**default, "description": "srv"}, None),
        ("port_profiles", None, None, False)]
    assert rollback.plan(now, {"fabric": {}}, rollback.Scope(devices=None))["operations"][0]["kind"] == "port_profiles"   # older snapshot


def test_a_copper_port_takes_a_fixed_speed_and_adopts_one():
    """Copper speed (auto by default or a fixed speed): leaf1 made a -48T6CQ."""
    nb = pinned(example_with_web(pool=False))
    device = run(nb.find_one("dcim/devices", name="leaf1"))
    run(nb.update("dcim/device-types", device["device_type"]["id"], {"model": "CE6881H-48T6CQ"}))
    for port in run(nb.find("dcim/interfaces", device_id=device["id"])):   # RJ45, as the switch reports
        if port["name"].startswith("10GE"):
            run(nb.update("dcim/interfaces", port["id"], {"type": "10gbase-t"}))
    running = RUNNING + "interface 10GE1/0/7\n negotiation disable\n speed 100\n#\ninterface 10GE1/0/8\n speed auto 1000 10000\n#\n"
    found = run(port_settings.adoption(nb, Annet({"leaf1": running}), "example"))
    adopted = {c["port"]: c["switch"]["speed"] for c in found["changes"]}
    assert adopted["10GE1/0/7"] == 100 and "10GE1/0/8" not in adopted   # `speed auto <list>` is not a fixed speed
    run(port_settings.set_enabled(nb, Annet({"leaf1": running}), "example", True))
    assert iface(nb, "leaf1", "10GE1/0/7")["speed"] == 100
    shown = run(port_settings.set_port(nb, "leaf1", "10GE1/0/3", speed=1000))
    assert shown["speed"] == 1000 and run(port_settings.get_port(nb, "leaf1", "10GE1/0/3"))["copper"]
    with pytest.raises(SegmentError, match="one of 10, 100, 1000, 10000"):
        run(port_settings.set_port(nb, "leaf1", "10GE1/0/3", speed=2500))
    run(port_settings.set_port(nb, "leaf1", "10GE1/0/3", speed=None))
    assert iface(nb, "leaf1", "10GE1/0/3")["speed"] is None
