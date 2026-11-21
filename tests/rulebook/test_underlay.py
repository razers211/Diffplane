"""Underlay patches on leaf1, in both directions, with our rulebook.

`old` holds only lines the underlay generators own, as Annet's ACL filtering would.
"""

import pytest

from tests.generators.harness import devices_from_fabric, render
from tests.rulebook.patch import patch, use_our_rulebook

# Top-level blocks the underlay generators own on leaf1.
UNDERLAY_BLOCKS = ("bfd", "ospf ", "interface LoopBack", "interface 100GE1/0/1")


def underlay_only(config: str) -> str:
    keep, out = False, []
    for line in config.splitlines():
        if not line.startswith(" "):
            keep = line.startswith(UNDERLAY_BLOCKS)
        if keep:
            out.append(line)
    return "\n".join(out) + "\n"


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


@pytest.fixture
def underlay() -> str:
    device = next(d for d in devices_from_fabric("tests/fixtures/example-small.yaml") if d.hostname == "leaf1")
    return underlay_only(render(device))


def test_add_creates_ospf_process_before_interfaces_join_it(underlay):
    lines = patch("bfd\n", underlay).splitlines()

    assert lines == [
        "ospf 1 router-id 10.10.0.2",
        " area 0.0.0.0",
        "  quit",
        " quit",
        "interface LoopBack0",
        " ip address 10.10.0.2 255.255.255.255",
        " ospf enable 1 area 0.0.0.0",
        " quit",
        "interface LoopBack1",
        " ip address 10.10.1.1 255.255.255.255",
        " ospf enable 1 area 0.0.0.0",
        " quit",
        "interface 100GE1/0/1",
        " undo portswitch",
        " ip address 10.11.0.2 255.255.255.252",
        " description to spine1 100GE1/0/1",
        " mtu 9216",
        " ospf network-type p2p",
        " ospf enable 1 area 0.0.0.0",
        " ospf bfd enable",
        " quit",
    ]


def test_remove_leaves_ospf_on_interfaces_before_removing_the_process(underlay):
    lines = patch(underlay, "").splitlines()

    assert lines.index(" undo ospf enable 1 area 0.0.0.0") < lines.index("undo ospf 1")
    assert lines.index(" portswitch") < lines.index("undo ospf 1")
    assert lines.index("undo ospf 1") < lines.index("undo interface LoopBack0")


def test_link_address_change_replaces_the_address(underlay):
    changed = underlay.replace("10.11.0.2 255.255.255.252", "10.11.0.6 255.255.255.252")

    lines = patch(underlay, changed).splitlines()

    assert lines == [
        "interface 100GE1/0/1",
        " undo ip address 10.11.0.2 255.255.255.252",
        " ip address 10.11.0.6 255.255.255.252",
        " quit",
    ]


def test_unchanged_underlay_needs_no_patch(underlay):
    assert patch(underlay, underlay) == ""
