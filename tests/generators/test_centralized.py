"""Centralized architecture: gateways on one M-LAG pair, the other
leaves layer 2 only; the same VPN model without L3 VNI; peering as inter-VPN static routes.
Forms verified on leaf1 (no-commit)."""

from functools import cache
from pathlib import Path

from tests.generators.harness import devices_from_fabric, patch_to, render
from tests.generators.test_golden import central_routing

CENTRAL = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example-central.yaml"


@cache
def routed() -> dict:
    return {d.hostname: d for d in devices_from_fabric(CENTRAL, prepare=central_routing)}


@cache
def bare() -> dict:
    return {d.hostname: d for d in devices_from_fabric(CENTRAL)}


def config(name: str) -> str:
    return render(routed()[name])


def test_the_gateway_pair_holds_every_gateway():
    for gw in ("c-gw1", "c-gw2"):
        text = config(gw)
        for vni, bd, vpn in ((10500, 500, "web-500"), (10600, 600, "db-600")):   # db is not attached on the gateways
            assert f"bridge-domain {bd}\n  vxlan vni {vni}\n" in text
            assert f"  vni {vni} head-end peer-list protocol bgp\n" in text
            assert f"interface Vbdif{bd}\n  ip binding vpn-instance {vpn}\n" in text
        assert "vxlan anycast-gateway enable" in text
        assert "arp collect host enable" not in text        # host routes over EVPN: symmetric only


def test_no_l3_vni_and_no_evpn_routes_between_vpn_instances():
    text = config("c-gw1")
    for absent in ("vxlan vni 1010500", "vxlan vni 1010600", "export-extcommunity evpn", "65002:1010500",
                   "advertise l2vpn evpn", "ipv4-family vpn-instance web-500", "ipv4-family vpn-instance db-600"):
        assert absent not in text, absent
    assert "ip vpn-instance web-500\n  ipv4-family\n    route-distinguisher" in text


def test_peering_and_the_firewall_are_inter_vpn_routes_on_the_gateways():
    text = config("c-gw2")
    web, db = "10.120.0.0 255.255.255.0", "10.120.1.0 255.255.255.0"
    assert f"ip route-static vpn-instance web-500 {db} vpn-instance db-600\n" in text
    assert f"ip route-static vpn-instance db-600 {web} vpn-instance web-500\n" in text
    assert f"ip route-static vpn-instance FW {web} vpn-instance web-500\n" in text
    assert "ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.6\n" in text
    assert "ip route-static vpn-instance db-600 0.0.0.0" not in text   # db has no firewall
    assert "    peer 10.255.0.6 as-number 65100\n" in text                 # firewall eBGP, as before


def test_the_other_leaves_are_layer_2_only():
    leaf1, leaf3 = config("c-leaf1"), config("c-leaf3")
    assert "bridge-domain 500\n" in leaf1 and "interface Eth-Trunk1.500 mode l2\n" in leaf1
    assert "bridge-domain 600\n" not in leaf1                # not attached there
    assert "bridge-domain 600\n" in leaf3 and "interface 10GE1/0/3.1 mode l2\n" in leaf3
    for text in (leaf1, leaf3):
        for absent in ("Vbdif", "ip vpn-instance web-500", "ip vpn-instance db-600", "export-extcommunity evpn",
                       "ip route-static vpn-instance"):
            assert absent not in text, absent


def test_routing_is_removed_from_the_gateways():
    lines = [line.strip() for line in patch_to(config("c-gw1"), bare()["c-gw1"]).splitlines()]
    for removal in ("undo interface Vbdif500", "undo interface Vbdif600", "undo ip vpn-instance web-500",
                    "undo ip vpn-instance db-600", "undo ip vpn-instance FW", "undo bridge-domain 600",
                    "undo ip route-static vpn-instance web-500 10.120.1.0 255.255.255.0 vpn-instance db-600"):
        assert removal in lines, "\n".join(lines)


def test_a_leaf_switched_to_centralized_loses_its_gateway(tmp_path):
    """Switching the architecture is a redeploy: a symmetric leaf's VBDIF, L3 VNI and
    EVPN families go when the fabric becomes centralized and the leaf is not a gateway."""
    import yaml

    from tests.generators.test_golden import ROOT

    async def web_on_leaf1(nb):
        from orchestrator.routing import Routing
        from orchestrator.segments import Segments

        await Segments(nb).create("example", "web", 10500, 500)
        await Segments(nb).attach("example", "leaf1", "Eth-Trunk1", 10500, 500)
        await Routing(nb).set_routing("example", 10500, True, firewall=None)

    source = ROOT / "seed" / "fabrics" / "example.yaml"
    data = yaml.safe_load(source.read_text())
    data["fabric"].update(architecture="centralized", gateway_pair=["leaf3", "leaf4"])
    data["leaf_pairs"][1]["role"] = "border-leaf"
    central = tmp_path / "example.yaml"
    central.write_text(yaml.safe_dump(data))

    before = {d.hostname: d for d in devices_from_fabric(source, prepare=web_on_leaf1)}
    after = {d.hostname: d for d in devices_from_fabric(central, prepare=web_on_leaf1)}
    running = render(before["leaf1"])
    assert "interface Vbdif500" in running and "vxlan vni 1010500" in running

    lines = [line.strip() for line in patch_to(running, after["leaf1"]).splitlines()]
    for removal in ("undo interface Vbdif500", "undo ip vpn-instance web-500"):
        assert removal in lines, "\n".join(lines)
    assert "undo vpn-instance web-500" not in lines   # VRP removes it with the instance
    assert not any("Eth-Trunk1.500" in line and "undo" in line for line in lines)   # the attachment stays
    assert "interface Vbdif500" in render(after["leaf3"])                           # now on the gateways


# -- a spine pair as gateway pair -------------------------------------------------------------

SPINES = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example-central-spines.yaml"


@cache
def spine_fabric() -> dict:
    from tests.generators.test_golden import spine_routing

    return {d.hostname: render(d) for d in devices_from_fabric(SPINES, prepare=spine_routing)}


def test_a_spine_pair_is_an_m_lag_gateway_pair_and_still_reflects_routes():
    spine = spine_fabric()["s-spine1"]
    for line in ("dfs-group 1\n", "interface Eth-Trunk0\n", "  peer-link 1\n", "  dfs-group 1 m-lag 1\n",
                 "interface LoopBack1\n", "interface Nve1\n", "  vni 10500 head-end peer-list protocol bgp\n",
                 "interface Vbdif500\n", "ip vpn-instance web-500\n", "interface Vlanif3000\n",
                 "  m-lag ip address 10.255.0.3 255.255.255.248\n", "    peer 10.255.0.6 as-number 65100\n",
                 "reserved for vxlan bypass", "reflect-client"):
        assert line in spine, line


def test_the_leaves_of_a_spine_gateway_fabric_are_layer_2_only():
    leaf = spine_fabric()["s-leaf1"]
    assert "bridge-domain 500\n" in leaf and "interface Eth-Trunk1.500 mode l2\n" in leaf
    for absent in ("Vbdif", "ip vpn-instance", "reflect-client"):
        assert absent not in leaf, absent
