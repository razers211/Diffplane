"""Switches added from the GUI generate like seeded ones, and the existing switches they
are cabled to pick up the new links."""

from functools import cache
from pathlib import Path

from orchestrator.fabric_build import FabricBuilder
from tests.generators.harness import devices_from_fabric, render

EXAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml"
MODEL = "CE6881H-48S6CQ"


async def grown(nb):
    build = FabricBuilder(nb, "example")
    await build.add_leaf_pair(["leaf3", "leaf4"], MODEL, "leaf", 0, ["100GE1/0/5", "100GE1/0/6"], [
        {"leaf": "leaf3", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/3"},
        {"leaf": "leaf4", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/4"}])


@cache
def devices():
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=grown)}


def test_a_new_pair_gets_the_full_leaf_config():
    config = render(devices()["leaf3"])
    for line in ("dfs-group 1", "interface Eth-Trunk0", "  peer-link 1", "interface Nve1", "interface LoopBack1",
                 "interface Vlanif4093", "bgp 65001", "ospf 1 router-id"):
        assert line in config, line


def test_the_spine_picks_up_the_new_links_and_peers():
    spine = render(devices()["spine1"])
    assert "interface 100GE1/0/3" in spine and "interface 100GE1/0/4" in spine
    leaf3_lo = next(ip.address for i in devices()["leaf3"].interfaces if i.name == "LoopBack0" for ip in i.ip_addresses)
    assert f"peer {leaf3_lo.split('/')[0]} as-number 65001" in spine


def test_the_existing_leaves_are_unchanged():
    from tests.generators.test_trunks import devices as example_devices

    assert render(devices()["leaf1"]) == render(example_devices()["leaf1"])


async def with_lone_leaf(nb):
    await FabricBuilder(nb, "example").add({
        "leaves": [{"name": "leaf5", "model": MODEL, "role": "leaf"}],
        "links": [{"spine": "spine1", "spine_port": "100GE1/0/6", "leaf": "leaf5", "leaf_port": "100GE1/0/1"}]})


def test_a_lone_leaf_is_a_vtep_without_mlag():
    from tests.generators.test_trunks import block

    found = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=with_lone_leaf)}
    config = render(found["leaf5"])
    vtep = next(ip.address for i in found["leaf5"].interfaces if i.name == "LoopBack1" for ip in i.ip_addresses)
    assert block(config, "interface Nve1") == f"source {vtep.split('/')[0]}"   # no pip-source, no MAC
    assert "evpn-overlay enable" in config and "interface LoopBack1" in config
    for absent in ("dfs-group", "stp v-stp", "peer-link", "Vlanif4093"):
        assert absent not in config, absent
    assert "interface 100GE1/0/6" in render(found["spine1"])


def central_lab(tmp_dir: Path) -> Path:
    """example.yaml made centralized, leaf1/leaf2 as the gateway pair."""
    import yaml

    data = yaml.safe_load(EXAMPLE.read_text())
    data["fabric"].update(architecture="centralized", gateway_pair=["leaf1", "leaf2"])
    data["leaf_pairs"][0]["role"] = "border-leaf"
    path = tmp_dir / "example.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


async def with_spine_pair(nb):
    await FabricBuilder(nb, "example").add({
        "spine_pairs": [{"spines": ["spine1", "spine2"], "model": MODEL, "peer_link_trunk": 0,
                         "peer_link_members": ["100GE1/0/5", "100GE1/0/6"]}],
        "links": [{"spine": "spine2", "spine_port": "100GE1/0/1", "leaf": "leaf1", "leaf_port": "100GE1/0/2"},
                  {"spine": "spine2", "spine_port": "100GE1/0/2", "leaf": "leaf2", "leaf_port": "100GE1/0/2"}]})


def test_a_spine_pair_of_a_centralized_fabric_is_mlag_and_vtep_but_no_gateway(tmp_path):
    from tests.generators.test_trunks import block

    found = {d.hostname: d for d in devices_from_fabric(central_lab(tmp_path), prepare=with_spine_pair)}
    for name in ("spine1", "spine2"):   # the existing spine and the new one
        config = render(found[name])
        for line in ("dfs-group 1", "interface Eth-Trunk0", "  peer-link 1", "interface Vlanif4093",
                     "interface LoopBack1", "evpn-overlay enable", "reflect-client"):
            assert line in config, (name, line)
        assert "pip-source" in block(config, "interface Nve1")
        assert "Vbdif" not in config and "ip vpn-instance" not in config
    vteps = {next(ip.address for i in found[n].interfaces if i.name == "LoopBack1" for ip in i.ip_addresses)
             for n in ("spine1", "spine2")}
    assert len(vteps) == 1   # one shared VTEP
