import copy
from pathlib import Path

import pytest
import yaml

from seed.model import FabricError, interface_type, load, parse

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"


def example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text())


def test_example_loads():
    fabric = load(EXAMPLE)

    assert fabric.slug == "example"
    assert [s.name for s in fabric.spines] == ["spine1", "spine2"]
    assert [p.leaves for p in fabric.leaf_pairs] == [("leaf1", "leaf2"), ("leaf3", "leaf4")]
    assert fabric.leaf_pairs[0].peer_link_name == "Eth-Trunk0"
    assert len(fabric.links) == 8
    assert fabric.services.config_context() == {
        "fabric": {
            "ntp_servers": ["192.0.2.1", "192.0.2.2"],
            "snmp": {"location": "example-dc", "contact": "noc@example.com"},
            "mlag": {"bypass_vlan": 4093},
            "gateway_mac": "0000-5e00-0101",
            "architecture": "symmetric",
        }
    }
    assert [(t.name, t.members) for t in fabric.leaf_pairs[0].mlag_trunks] == [("Eth-Trunk1", ("10GE1/0/1",))]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("10GE1/0/1", "10gbase-x-sfpp"),
        ("25GE1/0/48", "25gbase-x-sfp28"),
        ("100GE1/0/6", "100gbase-x-qsfp28"),
        ("GE1/0/1", "1000base-t"),
        ("Eth-Trunk10", "lag"),
        ("LoopBack1", "virtual"),
        ("MEth0/0/0", "1000base-t"),
        ("Vlanif100", "virtual"),
    ],
)
def test_interface_type(name, expected):
    assert interface_type(name) == expected


@pytest.mark.parametrize("name", ["Ethernet", "100GE1/0", "XGE1/0/1", "Eth-Trunk"])
def test_interface_type_rejects_unknown(name):
    with pytest.raises(FabricError):
        interface_type(name)


def broken(mutate) -> dict:
    data = copy.deepcopy(example())
    mutate(data)
    return data


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["links"][0].update(spine="spine9"), "not a spine"),
        (lambda d: d["links"][0].update(leaf="spine2"), "not a leaf"),
        (lambda d: d["links"][1].update(spine_port="100GE1/0/1"), "used more than once"),
        (lambda d: d["links"][0].update(leaf_port="100GE1/0/5"), "used more than once"),
        (lambda d: d["links"][0].update(leaf_port="Eth-Trunk1"), "physical port"),
        (lambda d: d["leaf_pairs"][0].update(leaves=["leaf1", "leaf1"]), "two distinct leaves"),
        (lambda d: d["leaf_pairs"][1].update(leaves=["leaf1", "leaf5"]), "more than once"),
        (lambda d: d["leaf_pairs"][0].update(role="spine"), "role must be"),
        (lambda d: d["leaf_pairs"][0]["peer_link"].update(members=[]), "at least one member"),
        (lambda d: d["fabric"]["prefixes"].update(vtep="10.0.0.128/25"), "overlaps"),
        (lambda d: d["fabric"]["prefixes"].update(p2p="10.1.0.0/31"), "too small"),
        (lambda d: d["fabric"]["prefixes"].update(p2p="10.1.0.1/24"), "p2p prefix"),
        (lambda d: d["fabric"].update(asn=0), "ASN"),
        (lambda d: d.pop("links"), "malformed"),
        (lambda d: d.pop("services"), "malformed"),
        (lambda d: d["fabric"].pop("mlag"), "bypass_vlan"),
        (lambda d: d["fabric"]["prefixes"].pop("mlag_bypass"), "mlag_bypass"),
        (lambda d: d["fabric"]["mlag"].update(bypass_vlan=4095), "2-4094"),
        (lambda d: d["fabric"].update(gateway_mac="00:00:5e:00:01:01"), "gateway_mac must look like"),
        (lambda d: d["fabric"].update(gateway_mac="0100-5e00-0101"), "multicast"),
        (lambda d: d["fabric"]["prefixes"].update(gateways="10.1.0.0/16"), "overlaps"),
        (lambda d: d["fabric"]["prefixes"].update(mlag_bypass="10.1.0.0/25"), "overlaps"),
        (lambda d: d["leaf_pairs"][0]["mlag_trunks"][0].update(trunk=0), "unique"),
        (lambda d: d["leaf_pairs"][0]["mlag_trunks"][0].update(members=["100GE1/0/5"]), "more than once"),
        (lambda d: d["leaf_pairs"][0]["mlag_trunks"][0].update(members=[]), "at least one member"),
        (lambda d: d.update(management={"leaf1": {"interface": "MEth0/0/0", "address": "10.9.0.1/24"}}), "set in the GUI"),
        (lambda d: d["fabric"].update(asn=4200000101), "symmetric fabric needs a 2-byte ASN"),
        (lambda d: d["services"].update(ntp_vpn_instance="bad name"), "VPN instance"),
        (lambda d: d["services"].update(ntp_servers=["ntp.example.com"]), "ntp_servers"),
        (lambda d: d["services"]["snmp"].update(location='dc "1"'), "single line"),
        (lambda d: d["services"]["snmp"].update(contact="a\nb"), "single line"),
        # the architecture and its gateway pair
        (lambda d: d["fabric"].update(architecture="asymmetric"), "architecture must be one of"),
        (lambda d: d["fabric"].update(gateway_pair=["leaf1", "leaf2"]), "centralized fabric only"),
        (lambda d: d["fabric"].update(architecture="centralized"), "needs fabric.gateway_pair"),
        (lambda d: d["fabric"].update(architecture="centralized", gateway_pair=["leaf1", "leaf3"]),
         "not one of the fabric's M-LAG pairs"),
        (lambda d: d["fabric"].update(architecture="centralized", gateway_pair=["leaf1", "leaf2"]), "border-leaf"),
        (lambda d: (d["fabric"].update(architecture="centralized", gateway_pair=["leaf2", "leaf1"]),
                    d["leaf_pairs"][0].update(role="border-leaf"), d["fabric"].pop("gateway_mac")), "gateway_mac"),
    ],
)
def test_validation_errors(mutate, message):
    with pytest.raises(FabricError, match=message):
        parse(broken(mutate))


def test_a_centralized_fabric_names_its_gateway_pair():
    data = copy.deepcopy(example())
    data["fabric"].update(architecture="centralized", gateway_pair=["leaf2", "leaf1"])
    data["leaf_pairs"][0]["role"] = "border-leaf"

    context = parse(data).services.config_context()["fabric"]

    assert context["architecture"] == "centralized" and context["gateway_pair"] == ["leaf1", "leaf2"]


def test_a_spine_pair_is_only_a_centralized_gateway_pair():
    from seed.model import load as load_file

    spines = load_file(EXAMPLE.parent / "example-central-spines.yaml")
    assert spines.spine_pair.leaves == ("s-spine1", "s-spine2") and spines.spine_pair.role == "spine"
    assert [p.leaves for p in spines.mlag_pairs][-1] == ("s-spine1", "s-spine2")

    data = yaml.safe_load((EXAMPLE.parent / "example-central-spines.yaml").read_text())
    data["fabric"].update(gateway_pair=["s-leaf1", "s-leaf2"])
    with pytest.raises(FabricError, match="a spine_pair is the gateway pair of a centralized fabric"):
        parse(data)
    data = yaml.safe_load((EXAMPLE.parent / "example-central-spines.yaml").read_text())
    data["spine_pair"]["spines"] = ["s-spine1", "s-leaf1"]
    with pytest.raises(FabricError, match="two distinct spines"):
        parse(data)


def test_a_fabric_may_have_no_ntp_servers():
    data = copy.deepcopy(example())
    data["services"].pop("ntp_servers")

    assert parse(data).services.config_context()["fabric"]["ntp_servers"] == []
