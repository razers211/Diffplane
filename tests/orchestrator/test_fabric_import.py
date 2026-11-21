"""Import an existing fabric: parse what the switches say, put the fabric together, and
check that the platform would send them nothing (the preview's patches are empty).

leaf1's data is its own (SSH probe; secret lines replaced by placeholders, its
public host key left out). The example fabric is imported from the configs our generators
make for it (tests/golden/example), as if its switches had been built by hand that way.
"""

from pathlib import Path

import pytest

from orchestrator import import_build
from orchestrator.fabric_import import ImportError_, assemble, other_host
from orchestrator.import_parse import parse_lldp, parse_switch, tree, vlan_numbers
from orchestrator.import_preview import preview

DATA = Path(__file__).parent / "data" / "import"
GOLDEN = Path(__file__).resolve().parents[1] / "golden"
MODEL = "CE6881H-48S6CQ"
VERSION = f"HUAWEI {MODEL} uptime is 9 days, 3 hours, 7 minutes\n"


def example_outputs() -> dict:
    # The switch as the platform generates it today: the fixture predates `advertise irb` (2026-11-20).
    config = (DATA / "leaf1.cfg").read_text().replace(
        "  undo policy vpn-target\n  peer 10.10.0.1 enable\n",
        "  undo policy vpn-target\n  peer 10.10.0.1 enable\n  peer 10.10.0.1 advertise irb\n", 1)
    return {"config": config, "version": (DATA / "version.txt").read_text(),
            "lldp": (DATA / "lldp-neighbor-brief.txt").read_text()}


@pytest.fixture
def community(monkeypatch):
    # The example fixture's community alias is this community's (generators/snmp.py community_alias);
    # the example's golden configs were made without one (tests/conftest.py).
    monkeypatch.setenv("SNMP_COMMUNITY", "test-community")


def test_the_config_tree_and_small_forms():
    root = tree("sysname a\n#\ninterface X\n description two  words \n#\nbgp 1\n peer 1.1.1.1 as-number 1\n #\n"
                " l2vpn-family evpn\n  undo policy vpn-target\n#\nreturn\n")
    assert [n.row for n in root.children] == ["sysname a", "interface X", "bgp 1"]
    assert root.children[1].rows() == ["description two words"]
    assert root.children[2].children[1].rows() == ["undo policy vpn-target"]
    assert vlan_numbers("3005 to 3007 4093".split()) == (3005, 3006, 3007, 4093)
    assert other_host(__import__("ipaddress").IPv4Interface("10.11.0.2/30")).compressed == "10.11.0.1/30"
    assert other_host(__import__("ipaddress").IPv4Interface("10.11.0.4/31")).compressed == "10.11.0.5/31"
    lldp = ("Local Interface         Exptime(s) Neighbor Interface            Neighbor Device\n"
            "-------------------------------------------------------------------------------------\n"
            "100GE1/0/1                   102     100GE1/0/1                    spine1\n")
    assert parse_lldp(lldp) == [("100GE1/0/1", "100GE1/0/1", "spine1")]
    assert parse_lldp((DATA / "lldp-neighbor-brief.txt").read_text()) == []   # the example has no neighbour


def test_leaf1_as_it_runs():
    s = parse_switch(**example_outputs())
    assert (s.name, s.model, s.version) == ("leaf1", MODEL, "CE6881H V300R023C00SPC500")
    assert s.loopback("LoopBack0").compressed == "10.10.0.2/32" and s.loopback("LoopBack1").compressed == "10.10.1.1/32"
    assert s.dfs.peer == "10.10.0.3" and s.dfs.priority == 150 and s.dfs.keyed
    assert s.nve == {"name": "Nve1", "source": "10.10.1.1", "pip_source": "10.10.0.2", "pip_peer": "10.10.0.3",
                     "mac": "0200-0a0a-0101", "vnis": []}
    assert s.bgp.asn == 65001 and s.bgp.peers["10.10.0.1"].families == {"ipv4": ["enable"], "evpn": ["enable", "advertise irb"]}
    assert not s.bgp.evpn_policy_vpn_target
    uplink = s.interfaces["100GE1/0/1"]
    assert (uplink.address.compressed, uplink.description, uplink.mtu, uplink.ospf) == (
        "10.11.0.2/30", "to spine1 100GE1/0/1", 9216, (1, "0.0.0.0"))
    assert s.interfaces["Eth-Trunk0"].peer_link and s.interfaces["Eth-Trunk1"].mlag == 1
    assert [r.row for r in s.static_routes] == ["ip route-static 10.10.0.3 255.255.255.255 10.12.0.2 preference 1"]
    assert (s.ntp_servers, s.ntp_vpn) == (("203.0.113.123", "203.0.113.124"), "_management_vpn_")
    assert s.snmp == {"location": "example-dc", "contact": "noc@example.com", "community": True}
    assert all(not i.other for i in s.interfaces.values())


def test_leaf1_gives_back_the_example_fabric():
    s = parse_switch(**example_outputs())
    plan = assemble({"leaf1": (s, "172.24.0.11/24")})
    assert plan.misfits == {} and plan.overrides == {}
    assert (plan.asn, plan.bypass_vlan, plan.architecture) == (65001, 4093, "symmetric")
    devices = {d.name: (d.role, d.planned, str(d.loopback)) for d in plan.devices.values()}
    assert devices == {"leaf1": ("leaf", False, "10.10.0.2/32"), "leaf2": ("leaf", True, "10.10.0.3/32"),
                       "spine1": ("spine", True, "10.10.0.1/32")}   # as seed/fabrics/example.yaml
    (pair,) = plan.pairs
    assert pair.devices == ("leaf1", "leaf2") and pair.peer_link_trunk == 0
    assert pair.peer_link_members == ("100GE1/0/5", "100GE1/0/6") and pair.mlag_trunks == {1: ("10GE1/0/1", "10GE1/0/2")}
    assert {d: str(a) for d, a in pair.bypass.items()} == {"leaf1": "10.12.0.1/30", "leaf2": "10.12.0.2/30"}
    (link,) = plan.links
    assert (link.spine, link.spine_port, link.leaf, link.leaf_port, str(link.network), link.found_by) == (
        "spine1", "100GE1/0/1", "leaf1", "100GE1/0/1", "10.11.0.0/30", "description")
    assert plan.pools == {"loopback": "10.10.0.0/24", "vtep": "10.10.1.0/24", "p2p": "10.11.0.0/24",
                          "mlag_bypass": "10.12.0.0/24", "gateways": None}


def test_lab_preview_sends_nothing(community):
    """The proof: importing leaf1, read-only, gives a fabric whose
    generated config is exactly what the switch runs."""
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"},
                      "switches": {"172.24.0.11/24": example_outputs()}})
    assert "error" not in result, result.get("error")
    assert result["patches"] == {"leaf1": ""}
    assert [d["name"] for d in result["plan"]["devices"] if d["planned"]] == ["leaf2", "spine1"]


def test_a_gateways_pool_is_asked_for():
    result = preview({"name": "example", "switches": {"172.24.0.11/24": example_outputs()}})
    assert "gateways pool" in result["error"]


def example_switches(change=None) -> dict:
    switches = {}
    for i, path in enumerate(sorted((GOLDEN / "example").glob("*.cfg"))):
        config = path.read_text()
        if change:
            config = change(path.stem, config)
        switches[f"192.0.2.{10 + i}/24"] = {"config": config, "version": VERSION, "lldp": ""}
    return switches


def test_example_fabric_round_trip():
    """Configs as our generators make them import to a fabric that reproduces them exactly:
    links found by their shared /30 (no LLDP given), pairs by their DFS peers."""
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": example_switches()})
    assert "error" not in result, result.get("error")
    assert result["patches"] == {name: "" for name in ("leaf1", "leaf2", "leaf3", "leaf4", "spine1", "spine2")}
    plan = result["plan"]
    assert plan["misfits"] == {} and plan["overrides"] == {}
    assert not any(d["planned"] for d in plan["devices"])
    assert {l["found_by"] for l in plan["links"]} == {"subnet"} and len(plan["links"]) == 8
    assert [p["devices"] for p in plan["pairs"]] == [["leaf1", "leaf2"], ["leaf3", "leaf4"]]


def _link_lines(config: str, change) -> str:
    """Apply `change` to the `ip address` line of each 100GE port (the fabric links)."""
    lines, port = [], False
    for line in config.splitlines():
        if not line.startswith(" "):
            port = line.startswith("interface 100GE")
        if port and line.strip().startswith("ip address "):
            line = change(line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def to_31(line: str) -> str:
    """10.1.0.1/30 -> 10.1.0.0/31, 10.1.0.2/30 -> 10.1.0.1/31."""
    address = line.split()[2]
    a, b, c, d = address.split(".")
    return line.replace(f"{address} 255.255.255.252", f"{a}.{b}.{c}.{int(d) - 1} 255.255.255.254")


def test_31_links_round_trip():
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"},
                      "switches": example_switches(lambda _, c: _link_lines(c, to_31))})
    assert "error" not in result, result.get("error")
    assert set(result["patches"].values()) == {""} and result["plan"]["misfits"] == {}
    assert {l["network"].split("/")[1] for l in result["plan"]["links"]} == {"31"}


def test_unnumbered_links_round_trip():
    def unnumbered(line):
        return line.split("ip address")[0] + "ip address unnumbered interface LoopBack0"

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16", "p2p": "10.1.0.0/24"},
                      "switches": example_switches(lambda _, c: _link_lines(c, unnumbered))})
    assert "error" not in result, result.get("error")
    assert set(result["patches"].values()) == {""} and result["plan"]["misfits"] == {}
    links = result["plan"]["links"]
    assert len(links) == 8 and all(l["unnumbered"] and l["found_by"] == "description" for l in links)


def test_unnumbered_on_one_end_is_a_misfit():
    def change(name, config):
        if name != "leaf1":
            return config
        return _link_lines(config, lambda l: l.split("ip address")[0] + "ip address unnumbered interface LoopBack0")

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": example_switches(change)})
    assert any("one end only" in m for m in result["plan"]["misfits"].get("leaf1", []))


def test_a_heartbeat_over_the_management_vpn_is_adopted():
    """An imported fabric: `dual-active detection source ip <MEth> vpn-instance
    _management_vpn_ peer <peer's MEth>` is kept as the switches have it; another address is a misfit."""
    meth = {"leaf1": ("192.0.2.10", "192.0.2.11"), "leaf2": ("192.0.2.11", "192.0.2.10")}

    def change(name, config, peer=None):
        if name not in meth:
            return config
        lines = [f"  dual-active detection source ip {meth[name][0]} vpn-instance _management_vpn_ "
                 f"peer {peer or meth[name][1]}" if "dual-active detection" in l else l for l in config.splitlines()]
        return "\n".join(lines) + "\n"

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": example_switches(change)})
    assert "error" not in result, result.get("error")
    assert [p["devices"] for p in result["plan"]["pairs"]] == [["leaf1", "leaf2"], ["leaf3", "leaf4"]]
    assert result["plan"]["overrides"]["devices"]["leaf1"]["heartbeat"] == {
        "source": "192.0.2.10", "vpn": "_management_vpn_", "peer": "192.0.2.11"}
    assert result["patches"]["leaf1"] == result["patches"]["leaf2"] == ""

    elsewhere = example_switches(lambda n, c: change(n, c, peer="192.0.2.99"))
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": elsewhere})
    assert any("heartbeat in VPN instance _management_vpn_" in m for m in result["plan"]["misfits"]["leaf1"])


def test_imported_underlay_forms_are_adopted():
    """An imported fabric: its DFS, OSPF, link, bypass VLAN, BGP and SNMP forms are
    adopted as values, so the import reproduces the switches exactly."""
    import re

    def change(name, config):
        config = re.sub(r"  authentication-mode hmac-sha256 password \S+\n", "", config)
        config = config.replace("dfs-group 1\n", "dfs-group 1\n  consistency-check enable mode loose\n")
        config = config.replace("  description vxlan-bypass\n", "  name VXLAN-BYPASS\n")
        config = re.sub(r"(ospf 1 router-id \S+\n)", r"\1  bfd all-interfaces enable\n  bfd all-interfaces min-tx-"
                        r"interval 500 min-rx-interval 500\n  lsa-arrival-interval intelligent-timer 50 50 50\n", config)
        config = config.replace("  mtu 9216\n", "").replace("  ospf bfd enable\n", "")
        config = config.replace("  ospf network-type p2p\n", "  ospf network-type p2p\n  ospf peer hold-max-cost timer 300000\n")
        config = re.sub(r"    peer \S+ reflect-client\n", "", config)
        config = config.replace("snmp-agent sys-info version v3 disable\n", "")
        config = config.replace("snmp-agent sys-info version v2c\n", "snmp-agent sys-info version v3\n")
        return config + ("traffic-segment enable\n" if name == "spine1" else "")

    roles = {"spine1": "spine", "spine2": "spine"}   # no reflect-client to tell (imported fabrics: given)
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": example_switches(change),
                      "roles": roles})
    assert "error" not in result, result.get("error")
    assert result["plan"]["misfits"] == {}
    assert result["plan"]["overrides"]["fabric"] == {
        "dfs_consistency_check": "loose", "dfs_authentication": False, "bypass_vlan_name": "VXLAN-BYPASS",
        "ospf_bfd_all_interfaces": [500, 500], "ospf_lsa_arrival": [50, 50, 50], "link_mtu": None,
        "link_ospf_bfd": False, "link_hold_max_cost": 300000, "reflect_clients": False, "snmp": False}
    assert result["plan"]["overrides"]["devices"]["spine1"] == {"traffic_segment_enable": True}
    assert result["patches"] == {name: "" for name in ("leaf1", "leaf2", "leaf3", "leaf4", "spine1", "spine2")}


def test_an_unnumbered_link_over_eth_trunks():
    """an imported switch: its uplinks are unnumbered Eth-Trunks with no
    `mode`, found by their members' LLDP neighbours, cabled member by member."""
    def trunk(number, peer):
        return (f"interface Eth-Trunk{number}\n  undo portswitch\n  description to {peer} 10GE1/0/20\n  mtu 9216\n"
                "  ip address unnumbered interface LoopBack0\n  ospf network-type p2p\n  ospf enable 1 area 0.0.0.0\n"
                f"  ospf bfd enable\ninterface 10GE1/0/20\n  eth-trunk {number}\ninterface 10GE1/0/21\n  eth-trunk {number}\n")

    def change(name, config):
        ends = {"spine1": ("10.1.0.1", "leaf1", 20), "leaf1": ("10.1.0.2", "spine1", 30)}
        if name not in ends:
            return config
        address, peer, number = ends[name]
        block = (f"interface 100GE1/0/1\n  undo portswitch\n  ip address {address} 255.255.255.252\n"
                 f"  description to {peer} 100GE1/0/1\n  mtu 9216\n  ospf network-type p2p\n"
                 "  ospf enable 1 area 0.0.0.0\n  ospf bfd enable\n")
        assert block in config
        return config.replace(block, trunk(number, peer))

    switches = example_switches(change)
    for outputs in switches.values():
        for name, peer in (("spine1", "leaf1"), ("leaf1", "spine1")):
            if f"sysname {name}\n" in outputs["config"]:
                outputs["lldp"] = "".join(f"{p}  110  {p}  {peer}\n" for p in ("10GE1/0/20", "10GE1/0/21"))
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": switches})
    assert "error" not in result, result.get("error")
    assert {"spine": "spine1", "spine_port": "Eth-Trunk20", "leaf": "leaf1", "leaf_port": "Eth-Trunk30",
            "network": None, "unnumbered": True, "found_by": "lldp"} in result["plan"]["links"]
    assert result["plan"]["overrides"]["devices"]["leaf1"]["manual_trunks"] == ["Eth-Trunk30"]
    assert result["patches"] == {name: "" for name in ("leaf1", "leaf2", "leaf3", "leaf4", "spine1", "spine2")}


def test_an_mlag_trunk_without_members_is_left_out():
    """an imported switch: Eth-Trunk5 in M-LAG 5 with no member port, absent on its peer."""
    def change(name, config):
        return config + "interface Eth-Trunk5\n  mode lacp-static\n  dfs-group 1 m-lag 5\n" if name == "leaf1" else config

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": example_switches(change)})
    assert "error" not in result, result.get("error")
    assert result["plan"]["misfits"] == {"leaf1": ["M-LAG 5 has no member ports: left out"]}


def test_bypass_ends_in_different_subnets_are_a_misfit():
    """an imported switch: 172.27.0.11/31 and 172.27.0.12/31, so neither next hop is on-link."""
    def change(name, config):
        return config.replace("10.2.0.2 255.255.255.252", "10.2.0.5 255.255.255.252") if name == "leaf2" else config

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"}, "switches": example_switches(change)})
    assert "error" not in result, result.get("error")
    for name in ("leaf1", "leaf2"):
        assert any("is not in the same subnet as its peer's" in m for m in result["plan"]["misfits"][name])


def test_values_that_differ_are_adopted():
    def change(name, config):
        if name == "leaf1":
            config = config.replace("priority 150", "priority 120")
            config = config.replace("description to spine1 100GE1/0/1", "description uplink-A")
        return config

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"},
                      "switches": example_switches(change)})
    assert result["plan"]["overrides"] == {"devices": {"leaf1": {
        "dfs_priority": 120, "descriptions": {"100GE1/0/1": "uplink-A"}}}}
    assert set(result["patches"].values()) == {""}   # the generators use them


def test_fabric_wide_values_are_adopted():
    def change(name, config):
        config = config.replace("ospf 1 router-id", "ospf 10 router-id").replace("enable 1 area", "enable 10 area")
        config = config.replace("dfs-group 1", "dfs-group 2")
        return config.replace("mac-address 0200-0a00-0101", "mac-address 0200-fc00-0001")

    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16"},
                      "switches": example_switches(change)})
    assert result["plan"]["overrides"]["fabric"] == {"dfs_group": 2, "ospf_process": 10}
    assert result["plan"]["overrides"]["devices"] == {"leaf1": {"nve_mac": "0200-fc00-0001"},
                                                      "leaf2": {"nve_mac": "0200-fc00-0001"}}
    assert set(result["patches"].values()) == {""}


def test_one_fabric_asn():
    def change(name, config):
        return config.replace("bgp 65001", "bgp 65002") if name == "spine2" else config

    result = preview({"name": "example", "switches": example_switches(change)})
    assert "different ASes" in result["error"]


def test_addresses_outside_the_pools_are_refused():
    result = preview({"name": "example", "pools": {"gateways": "10.100.0.0/16", "p2p": "10.99.0.0/24"},
                      "switches": {"172.24.0.11/24": example_outputs()}})
    assert "outside the pool 10.99.0.0/24" in result["error"]


def test_builder_input_places_the_switches_addresses():
    plan = assemble({"leaf1": (parse_switch(**example_outputs()), "172.24.0.11/24")})
    settings, batch, addresses, prefixes = import_build.builder_input(plan, "example", {"gateways": "10.100.0.0/16"})
    assert settings["asn"] == 65001 and settings["prefixes"]["p2p"] == "10.11.0.0/24"
    assert batch["pairs"][0]["management"] == {"leaf1": "172.24.0.11/24"}
    assert batch["pairs"][0]["mlag_trunks"] == [{"trunk": 1, "members": ["10GE1/0/1", "10GE1/0/2"]}]
    assert batch["spines"] == [{"name": "spine1", "model": MODEL, "management": None}]
    assert prefixes == {"spine1 100GE1/0/1 - leaf1 100GE1/0/1": "10.11.0.0/30",
                        "leaf1-leaf2 M-LAG bypass": "10.12.0.0/30"}
    assert addresses[("leaf2", "Vlanif4093")] == "10.12.0.2/30"
    assert addresses[("spine1", "100GE1/0/1")] == "10.11.0.1/30"


def test_sysnames_must_differ():
    outputs = example_outputs()
    result = preview({"name": "example", "switches": {"172.24.0.11/24": outputs, "172.24.0.105/24": outputs}})
    assert "both have sysname leaf1" in result["error"]
    with pytest.raises(ImportError_):
        import_build.builder_input(assemble({}), "x")


# -- tenants: fabrics built through the orchestrator, rendered, and imported back ---------------

def generated(fabric: str, prepare=None) -> dict:
    """Every switch's config as our generators make it for a seeded fabric (+ tenants)."""
    from tests.generators.harness import devices_from_fabric, render

    root = Path(__file__).resolve().parents[2]
    devices = devices_from_fabric(root / "seed" / "fabrics" / f"{fabric}.yaml", prepare=prepare)
    return {f"192.0.2.{10 + i}/24": {"config": render(d), "version": VERSION, "lldp": ""}
            for i, d in enumerate(sorted(devices, key=lambda d: d.hostname))}


def round_trip(name: str, switches: dict, **choices) -> dict:
    result = preview({"name": name, "switches": switches, **choices})
    assert "error" not in result, result.get("error")
    assert result["plan"]["misfits"] == {}
    assert {n: p for n, p in result["patches"].items() if p} == {}
    return result["plan"]


def test_routed_segments_services_and_peering_round_trip():
    from tests.generators.test_golden import example_routing

    plan = round_trip("example", generated("example", example_routing))
    # names from the VPN instances <name>-<bd>
    assert [(s["vni"], s["bd_id"], s["name"], s["gateway"], s["firewall"], s["peers"]) for s in plan["segments"]] == [
        (10500, 500, "web", "10.100.0.1/24", "FW", [10600]),
        (10600, 600, "db", "10.100.1.1/24", None, [10500]),
        (10700, 700, "app", "10.100.2.1/24", "FW", []),          # routed, attached nowhere
    ]
    assert plan["services"] == [{"name": "FW", "devices": ["leaf1"], "port": "10GE1/0/4", "vlan": 3000,
                                 "address": "10.255.0.1/30", "next_hop": "10.255.0.2", "asn": 65100,
                                 "kind": "firewall"}]
    assert sorted((a["device"], a["port"], a["vni"], a["vid"]) for a in plan["attachments"]) == [
        ("leaf1", "10GE1/0/3", 10600, None), ("leaf1", "Eth-Trunk1", 10500, 500),
        ("leaf2", "Eth-Trunk1", 10500, 500)]
    assert plan["architecture"] == "symmetric" and plan["pools"]["gateways"] == "10.100.0.0/16"


def test_centralized_fabric_round_trip():
    from tests.generators.test_golden import central_routing

    plan = round_trip("example-central", generated("example-central", central_routing))
    assert plan["architecture"] == "centralized" and plan["gateway_pair"] == ["c-gw1", "c-gw2"]
    assert {d["name"]: d["role"] for d in plan["devices"]}["c-gw1"] == "border-leaf"
    (service,) = plan["services"]
    assert (service["devices"], service["port"], service["address"], service["next_hop"]) == (
        ["c-gw1", "c-gw2"], "Eth-Trunk1", "10.255.0.3/29", "10.255.0.6")


def test_imported_segment_scheme_is_adopted():
    """An imported fabric: a segment's own VPN name, RD, L3 VNI and route target, L2
    route target, gateway MAC and ARP forms, and untagged sub-interfaces numbered by bridge domain,
    are adopted as the segment's values in a centralized fabric, so the import reproduces them."""
    import re

    from tests.generators.test_golden import central_routing

    def change(config):
        config = re.sub(r"(ip vpn-instance db-600\n  ipv4-family\n)    route-distinguisher \S+\n",
                        r"\1    route-distinguisher 600:600\n    vpn-target 23600:23600 export-extcommunity evpn\n"
                        r"    vpn-target 23600:23600 import-extcommunity evpn\n  vxlan vni 20600\n", config)
        config = config.replace("db-600", "DB-NET")
        gateway = "interface Vbdif600\n" in config
        config = config.replace("    vpn-target 65002:10600 import-extcommunity\n",
                                "    vpn-target 10.120.1.0:600 import-extcommunity\n"
                                + ("    vpn-target 23600:23600 export-extcommunity\n" if gateway else
                                   "  arp l2-proxy enable\n  arp collect host enable\n"))
        config = config.replace("65002:10600 export", "10.120.1.0:600 export")
        config = re.sub(r"(interface Vbdif600\n.*\n.*\n)  mac-address \S+\n  vxlan anycast-gateway enable\n",
                        r"\1  arp broadcast-detect enable\n  mac-address 0200-fc00-0600\n  arp collect host enable\n",
                        config)
        return config.replace("10GE1/0/3.1 mode l2", "10GE1/0/3.600 mode l2")

    switches = generated("example-central", central_routing)
    for outputs in switches.values():
        outputs["config"] = change(outputs["config"])
    plan = round_trip("example-central", switches)
    assert plan["overrides"]["segments"] == {"10600": {
        "l2_rt": "10.120.1.0:600", "bd_arp": True, "anycast": False, "arp_broadcast_detect": True,
        "arp_collect_host": True, "gateway_mac": "0200-fc00-0600", "vrf": "DB-NET", "rd": "600:600",
        "l3_vni": 20600, "l3_rt": "23600:23600"}}
    assert plan["overrides"]["fabric"]["untagged_subif"] == "bd"
    assert {s["vni"]: s["name"] for s in plan["segments"]} == {10500: "web", 10600: "DB-NET"}


def imported_services(config):
    """example-central's FW service in an imported fabric's firewall contexts forms: RD N:N, the shared address as the
    main one, `arp proxy enable`, its own MAC, iBGP over the Vlanif without BFD; and a VPN instance
    bound to no interface."""
    import re

    config = re.sub(r"route-distinguisher \S+:3000\n", "route-distinguisher 3000:3000\n", config)
    config = re.sub(r"  ip address (10.255.0.\d) 255.255.255.248\n  mac-address \S+\n  m-lag ip address 10.255.0.3 "
                    r"255.255.255.248\n", r"  ip address 10.255.0.3 255.255.255.248\n  arp proxy enable\n  mac-address "
                    r"0200-fc00-3000\n  m-lag ip address \1 255.255.255.248\n", config)
    config = config.replace("    peer 10.255.0.6 as-number 65100\n    peer 10.255.0.6 bfd enable\n",
                            "    peer 10.255.0.6 as-number 65002\n    peer 10.255.0.6 connect-interface Vlanif3000\n")
    return config.replace("ip vpn-instance FW\n", "ip vpn-instance KEEP\nip vpn-instance FW\n")


def test_imported_service_forms_are_adopted():
    from tests.generators.test_golden import central_routing

    switches = generated("example-central", central_routing)
    for outputs in switches.values():
        outputs["config"] = imported_services(outputs["config"])
    plan = round_trip("example-central", switches)
    assert plan["overrides"]["services"] == {"FW": {"rd": "3000:3000", "arp_proxy": True, "mac": "0200-fc00-3000",
                                                    "connect_interface": True, "bfd": False, "shared_main": True}}
    assert plan["overrides"]["devices"]["c-gw1"]["kept_vpns"] == ["KEEP"]
    (service,) = plan["services"]
    assert (service["address"], service["asn"], service["next_hop"]) == ("10.255.0.3/29", 65002, "10.255.0.6")


def test_a_default_route_to_another_next_hop_is_a_misfit():
    from tests.generators.test_golden import central_routing

    switches = generated("example-central", central_routing)
    for outputs in switches.values():
        if "sysname c-gw2" in outputs["config"]:
            outputs["config"] = outputs["config"].replace("vpn-instance FW 10.255.0.6", "vpn-instance FW 10.255.0.5")
    result = preview({"name": "example-central", "switches": switches})
    assert any("default routes via FW 10.255.0.5, via FW 10.255.0.6" in m for m in result["plan"]["misfits"]["fabric"])
    assert {s["vni"]: s["firewall"] for s in result["plan"]["segments"]}[10500] is None


def test_imported_vlan_trunks_and_spare_config_are_kept():
    """An imported fabric: server trunk descriptions, a bridge domain with no port, an
    Nve VNI with no bridge domain and a VLAN nothing uses are kept as each switch has them; a VLAN
    trunk is kept only on a border switch, so a leaf's is a misfit."""
    from tests.generators.test_golden import central_routing

    bd500 = ("bridge-domain 500\n  vxlan vni 10500\n  evpn\n    route-distinguisher auto\n"
             "    vpn-target 65002:10500 export-extcommunity\n    vpn-target 65002:10500 import-extcommunity\n")
    switches = generated("example-central", central_routing)
    for outputs in switches.values():
        config = outputs["config"]
        if "sysname c-leaf3" in config:
            config = config.replace("vlan batch 4093\n", "vlan batch 777 4093\n")
            config = config.replace("\nbridge-domain 600\n", "\n" + bd500 + "bridge-domain 600\n")
            config = config.replace("  vni 10600 head-end peer-list protocol bgp\n",
                                    "  vni 10500 head-end peer-list protocol bgp\n  vni 10600 head-end peer-list "
                                    "protocol bgp\n  vni 10777 head-end peer-list protocol bgp\n")
        if "sysname c-leaf1" in config:
            config = config.replace("interface Eth-Trunk1\n  mode", "interface Eth-Trunk1\n  description To-SRV\n  mode")
        outputs["config"] = config
    plan = round_trip("example-central", switches)
    assert plan["overrides"]["devices"]["c-leaf3"] == {
        "segments_without_ports": [10500], "kept_vnis": [10777], "kept_vlans": [777]}
    assert plan["overrides"]["devices"]["c-leaf1"] == {"descriptions": {"Eth-Trunk1": "To-SRV"}}


def test_a_vlan_trunk_is_kept_on_a_border_switch_and_a_misfit_on_a_leaf():
    """only border switches keep VLAN trunks, for services."""
    from tests.generators.test_golden import central_routing

    trunk = ("interface 10GE1/0/10\n  port link-type trunk\n  undo port trunk allow-pass vlan 1\n"
             "  port trunk allow-pass vlan 2 to 4094\n")
    switches = generated("example-central", central_routing)
    for outputs in switches.values():
        if "sysname c-leaf3\n" in outputs["config"]:
            outputs["config"] += trunk
    leaf = preview({"name": "example-central", "switches": switches})["plan"]
    assert "vlan_trunks" not in leaf["overrides"].get("devices", {}).get("c-leaf3", {})
    assert any("a VLAN trunk on a leaf" in m for m in leaf["misfits"]["c-leaf3"])
    border = preview({"name": "example-central", "switches": switches, "roles": {"c-leaf3": "border-leaf"}})["plan"]
    assert border["overrides"]["devices"]["c-leaf3"]["vlan_trunks"] == {"10GE1/0/10": {"vlans": "2 to 4094", "undo_vlan1": True}}


def test_spine_gateway_pair_round_trip():
    from tests.generators.test_golden import spine_routing

    plan = round_trip("example-central-spines", generated("example-central-spines", spine_routing))
    assert plan["gateway_pair"] == ["s-spine1", "s-spine2"]


def test_pip_layout_round_trip():
    """An imported fabric: PIP on LoopBack1, the pair's VTEP on LoopBack2."""
    plan = round_trip("example-pip", generated("example-pip"), pools={"gateways": "10.140.0.0/16"},
                      gateway_mac="0000-5e00-0104")
    devices = {d["name"]: d for d in plan["devices"]}
    assert (devices["p-leaf1"]["pip"], devices["p-leaf1"]["vtep"]) == ("10.40.1.3/32", "10.40.2.2/32")
    assert devices["p-leaf2"]["vtep"] == "10.40.2.2/32"
    assert (plan["pools"]["pip"], plan["pools"]["vtep"]) == ("10.40.1.0/24", "10.40.2.0/24")
    assert plan["gateway_pair"] == ["p-spine1", "p-spine2"]


def test_a_bridge_domain_in_the_management_vpn_is_recorded_and_left_alone():
    """An imported fabric's management bridge domain carries the management network: never a segment; each
    switch's part of it is recorded and no patch touches it."""
    from tests.generators.test_golden import example_routing

    switches = generated("example", example_routing)
    for outputs in switches.values():
        outputs["config"] = outputs["config"].replace("interface Vbdif500\n  ip binding vpn-instance web-500",
                                                      "interface Vbdif500\n  ip binding vpn-instance _management_vpn_")
    result = preview({"name": "example", "switches": switches})
    assert 10500 not in [s["vni"] for s in result["plan"]["segments"]]
    assert result["plan"]["overrides"]["devices"]["leaf1"]["management"] == {
        "bridge_domains": [500], "vnis": [10500], "interfaces": ["Eth-Trunk1.500"]}
    patch = result["patches"]["leaf1"]
    for line in ("bridge-domain 500", "Vbdif500", "Eth-Trunk1.500", "vni 10500"):
        assert line not in patch, patch


def test_a_dropped_bridge_domain_is_not_imported_and_would_be_removed():
    """An imported fabric's TEST segments: left out at the operator's choice."""
    from tests.generators.test_golden import example_routing

    result = preview({"name": "example", "switches": generated("example", example_routing), "drop": [500]})
    assert 10500 not in [s["vni"] for s in result["plan"]["segments"]]
    assert not any("web-500" in m for m in result["plan"]["misfits"].get("leaf1", []))
    for line in ("undo bridge-domain 500", "undo interface Vbdif500", "undo ip vpn-instance web-500"):
        assert line in result["patches"]["leaf1"], result["patches"]["leaf1"]


def test_a_centralized_fabric_without_routed_segments_is_said_by_the_operator():
    switches = generated("example-central")
    nothing_routed = {"pools": {"gateways": "10.200.0.0/16"}, "gateway_mac": "0000-5e00-0102"}
    plan = round_trip("example-central", switches, architecture="centralized", gateway_pair=["c-gw1", "c-gw2"],
                      **nothing_routed)
    assert plan["architecture"] == "centralized"
    plan = round_trip("example-central-spines", generated("example-central-spines"), **nothing_routed)   # a spine pair says it
    assert plan["architecture"] == "centralized"


def test_single_trunks_and_vlan_based_ports():
    from orchestrator.trunks import Trunks

    async def prepare(nb):
        await Trunks(nb).create("leaf1", 7, ["10GE1/0/7", "10GE1/0/8"], mlag=False)

    switches = generated("example", prepare)
    plan = round_trip("example", switches, pools={"gateways": "10.200.0.0/16"})
    assert plan["trunks"] == [{"device": "leaf1", "name": "Eth-Trunk7", "members": ["10GE1/0/7", "10GE1/0/8"]}]
    leaf1 = next(k for k, v in switches.items() if "sysname leaf1" in v["config"])
    switches[leaf1]["config"] += "interface 10GE1/0/9\n  port link-type access\n  port default vlan 10\n"
    result = preview({"name": "example", "switches": switches, "pools": {"gateways": "10.200.0.0/16"}})
    assert any("VLAN-based port" in m for m in result["plan"]["misfits"]["leaf1"])


def test_an_unnumbered_port_is_a_fabric_link():
    from orchestrator.segments import Segments
    from orchestrator.trunks import Trunks

    port = {"id": 7, "name": "100GE1/0/1", "type": {"value": "100gbase-x-qsfp28"}, "tags": [{"slug": "unnumbered"}]}
    assert "fabric link" in Segments._refusal("leaf1", port, routed=False)
    assert not Trunks._free(port, routed=set(), services=set())


def test_an_m_lag_trunk_gets_the_union_of_its_sides_vlans():
    """An imported fabric's spines: an M-LAG trunk whose sides pass different VLANs gets both."""
    from tests.generators.test_golden import central_routing

    switches = generated("example-central", central_routing)
    for outputs in switches.values():
        for name, vlans in (("c-leaf1", "701 to 704"), ("c-leaf2", "2 to 4094")):
            if f"sysname {name}\n" in outputs["config"]:
                outputs["config"] = outputs["config"].replace(
                    "interface Eth-Trunk1\n  mode", f"interface Eth-Trunk1\n  port link-type trunk\n"
                    f"  port trunk allow-pass vlan {vlans}\n  mode")
    roles = {"c-leaf1": "border-leaf", "c-leaf2": "border-leaf"}
    plan = preview({"name": "example-central", "switches": switches, "roles": roles})["plan"]
    assert [plan["overrides"]["devices"][n]["vlan_trunks"] for n in ("c-leaf1", "c-leaf2")] == [
        {"Eth-Trunk1": {"vlans": "2 to 4094"}}] * 2
    assert any("both get 2 to 4094" in m for m in plan["misfits"]["fabric"])


def test_a_crossed_peer_link_is_cabled_as_lldp_sees_it():
    """An imported fabric's spines: 10GE1/0/47 of one goes to 10GE1/0/48 of the other."""
    import asyncio

    from orchestrator.memory_netbox import FakeNetBox

    switches = generated("example-central")
    crossed = {"c-leaf1": "c-leaf2", "c-leaf2": "c-leaf1"}
    for outputs in switches.values():
        name = next((n for n in crossed if f"sysname {n}\n" in outputs["config"]), None)
        if name:
            outputs["lldp"] = (f"100GE1/0/5  110  100GE1/0/6  {crossed[name]}\n"
                               f"100GE1/0/6  110  100GE1/0/5  {crossed[name]}\n")
    parsed = {}
    for address, outputs in switches.items():
        s = parse_switch(outputs["config"], outputs["version"], outputs["lldp"])
        parsed[s.name] = (s, address)
    plan = assemble(parsed)
    pair = next(p for p in plan.pairs if p.devices == ("c-leaf1", "c-leaf2"))
    assert pair.cabling == (("100GE1/0/5", "100GE1/0/6"), ("100GE1/0/6", "100GE1/0/5"))
    assert all(not p.cabling for p in plan.pairs if p is not pair)

    nb = FakeNetBox()
    asyncio.run(import_build.build(nb, plan, "example-central", {"gateways": "10.100.0.0/16"}))

    async def peers(device: str, port: str) -> list[str]:
        d = await nb.find_one("dcim/devices", name=device)
        iface = await nb.find_one("dcim/interfaces", device_id=d["id"], name=port)
        return [f"{p['device']['name']} {p['name']}" for p in iface.get("link_peers") or []]

    assert asyncio.run(peers("c-leaf1", "100GE1/0/5")) == ["c-leaf2 100GE1/0/6"]
    assert asyncio.run(peers("c-leaf1", "100GE1/0/6")) == ["c-leaf2 100GE1/0/5"]
