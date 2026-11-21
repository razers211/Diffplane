"""Eth-Trunks from the GUI: M-LAG trunks on a pair (Mlag), single
trunks on a leaf or a spine (Trunks), LACP static only. Deleted trunks are retired with their
members until a verified deploy; hand-made trunks are never touched."""

from functools import cache
from pathlib import Path

from orchestrator.trunks import Trunks
from tests.generators.harness import devices_from_fabric, patch_to, render

EXAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml"


async def with_trunks(nb):
    trunks = Trunks(nb)
    await trunks.create("leaf1", 2, ["10GE1/0/5", "10GE1/0/6"], mlag=False)
    await trunks.create("leaf1", 3, ["10GE1/0/7"], mlag=True)
    await trunks.create("spine1", 4, ["10GE1/0/10"], mlag=False)


async def deleted(nb):
    await with_trunks(nb)
    await Trunks(nb).delete("leaf1", "Eth-Trunk2")
    await Trunks(nb).delete("leaf1", "Eth-Trunk3")


@cache
def devices(prepare=None):
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=prepare)}


def block(config: str, header: str) -> str:
    """The lines of one top-level block of a rendered config."""
    lines = config.splitlines()
    start = lines.index(header)
    body = []
    for line in lines[start + 1:]:
        if not line.startswith(" "):
            break
        body.append(line.strip())
    return "\n".join(body)


def test_single_trunk_is_lacp_static_without_mlag():
    config = render(devices(with_trunks)["leaf1"])
    assert block(config, "interface Eth-Trunk2") == "mode lacp-static"
    assert block(config, "interface 10GE1/0/5") == "eth-trunk 2"
    assert block(config, "interface 10GE1/0/6") == "eth-trunk 2"


def test_mlag_trunk_is_bound_to_the_pairs_mlag_on_both_leaves():
    found = devices(with_trunks)
    for leaf in ("leaf1", "leaf2"):
        config = render(found[leaf])
        assert block(config, "interface Eth-Trunk3") == "mode lacp-static\ndfs-group 1 m-lag 3", leaf
        assert block(config, "interface 10GE1/0/7") == "eth-trunk 3", leaf
    assert "Eth-Trunk2" not in render(found["leaf2"])


def test_single_trunk_on_a_spine():
    config = render(devices(with_trunks)["spine1"])
    assert block(config, "interface Eth-Trunk4") == "mode lacp-static"
    assert block(config, "interface 10GE1/0/10") == "eth-trunk 4"


def test_adding_trunks_creates_the_trunk_before_its_members():
    leaf = devices(with_trunks)["leaf1"]
    lines = patch_to(render(devices()["leaf1"]), leaf).splitlines()
    assert lines.index("interface Eth-Trunk2") < lines.index("interface 10GE1/0/5")
    assert " eth-trunk 2" in lines and " dfs-group 1 m-lag 3" in lines


def test_deleted_trunks_are_removed_after_their_members_left():
    after = devices(deleted)["leaf1"]
    lines = patch_to(render(devices(with_trunks)["leaf1"]), after).splitlines()
    assert "undo interface Eth-Trunk2" in lines and "undo interface Eth-Trunk3" in lines
    member = lines.index("interface 10GE1/0/5")
    assert lines[member + 1] == " undo eth-trunk"
    assert member < lines.index("undo interface Eth-Trunk2")
    assert lines.index("interface 10GE1/0/7") < lines.index("undo interface Eth-Trunk3")
    # Verified and gone: once retired_trunks is cleared, nothing is left to patch.
    assert patch_to(render(after), after) == ""


def test_hand_made_trunks_are_left_alone():
    leaf = devices(with_trunks)["leaf1"]
    running = render(leaf) + "interface Eth-Trunk9\n  mode lacp-static\ninterface 10GE1/0/20\n  eth-trunk 9\n"
    assert patch_to(running, leaf) == ""


def test_the_lab_trunk_stays_bound_to_mlag():
    """Eth-Trunk1 comes from the example YAML with the m-lag tag: unchanged golden output."""
    config = render(devices()["leaf1"])
    assert block(config, "interface Eth-Trunk1") == "mode lacp-static\ndfs-group 1 m-lag 1"


async def service_on_single_trunk(nb):
    from orchestrator.routing import Routing

    await Trunks(nb).create("leaf1", 2, ["10GE1/0/5"], mlag=False)
    await Routing(nb).create_service("example", "FW", "leaf1", "Eth-Trunk2", 3000, "10.255.0.1/29", "10.255.0.6")


def test_service_on_a_single_trunk():
    """The trunk is an L2 trunk for the service VLAN; the Vlanif has one plain address (no M-LAG)."""
    found = devices(service_on_single_trunk)
    config = render(found["leaf1"])
    assert set(block(config, "interface Eth-Trunk2").splitlines()) == {
        "port link-type trunk", "port trunk allow-pass vlan 3000", "mode lacp-static"}
    vlanif = block(config, "interface Vlanif3000")
    assert "ip address 10.255.0.1 255.255.255.248" in vlanif and "m-lag" not in vlanif
    assert "Vlanif3000" not in render(found["leaf2"])
