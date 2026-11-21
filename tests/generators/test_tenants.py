"""Tenant generators: VPN instances, anycast gateways, BGP, firewall services."""

import textwrap
from functools import cache
from pathlib import Path

import pytest
from annet.annlib.patching import match_row_to_acl
from annet.annlib.rbparser.acl import compile_acl_text
from annet.generators.exceptions import GeneratorError

from tests.generators.harness import devices_from_fabric, render
from tests.generators.test_golden import example_routing, example_segments

EXAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml"


@cache
def routed() -> dict:
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=example_routing)}


def acl(generator_class, device):
    return compile_acl_text(textwrap.dedent(generator_class(device.storage).acl_huawei(device)), "huawei")


def test_unrouted_segments_get_no_tenant_config():
    leaf = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=example_segments)}["leaf1"]
    config = render(leaf)
    assert "ip vpn-instance" not in config
    assert "Vbdif" not in config


def test_gateway_only_where_the_segment_is_attached():
    leaf2 = render(routed()["leaf2"])
    assert "interface Vbdif500\n" in leaf2         # web on the M-LAG trunk: both leaves
    assert "interface Vbdif600\n" not in leaf2     # db on a leaf1 port only


def test_l3_vni_is_l2_vni_plus_one_million_and_bridge_domain_only_exports_it():
    config = render(routed()["leaf2"])
    assert "  vxlan vni 1010500\n" in config
    assert "    vpn-target 65001:1010500 export-extcommunity\n" in config
    assert "vpn-target 65001:1010500 import-extcommunity\n" not in config  # only with "evpn" (VPN instance)


def test_peering_is_symmetric():
    config = render(routed()["leaf1"])
    seg500 = config.split("ip vpn-instance web-500\n")[1].split("ip vpn-instance")[0]
    seg600 = config.split("ip vpn-instance db-600\n")[1].split("ip vpn-instance")[0]
    assert "vpn-target 65001:1010600 import-extcommunity evpn" in seg500
    assert "vpn-target 65001:1010500 import-extcommunity evpn" in seg600


def test_static_routes_and_import_static_only_on_the_firewall_leaf():
    leaf1, leaf2 = render(routed()["leaf1"]), render(routed()["leaf2"])
    assert "ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2\n" in leaf1
    assert "ip route-static vpn-instance" not in leaf2
    assert "import-route static" not in leaf2


def test_segment_without_firewall_gets_no_default_route():
    assert "db-600 0.0.0.0" not in render(routed()["leaf1"])


def test_gateway_needs_the_fabric_mac():
    device = routed()["leaf2"]
    saved = device.config_context["fabric"].pop("gateway_mac")
    try:
        with pytest.raises(GeneratorError):
            render(device)
    finally:
        device.config_context["fabric"]["gateway_mac"] = saved


def test_firewall_acl_never_owns_the_management_vpn_routes():
    from generators.firewall_services import FirewallServices

    from generators.vlanifs import Vlanifs

    rules = acl(FirewallServices, routed()["leaf1"])
    assert match_row_to_acl("ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500",
                            rules)[0] is not None
    assert match_row_to_acl("ip route-static vpn-instance _management_vpn_ 0.0.0.0 0 172.24.0.1", rules)[0] is None
    assert match_row_to_acl("ip route-static 0.0.0.0 0 172.24.0.1", rules)[0] is None
    assert match_row_to_acl("interface MEth0/0/0", acl(Vlanifs, routed()["leaf1"]))[0] is None


def test_vrf_acl_never_owns_the_management_vpn_instance():
    from generators.tenant_vrfs import TenantVrfs

    rules = acl(TenantVrfs, routed()["leaf1"])
    assert match_row_to_acl("ip vpn-instance web-500", rules)[0] is not None
    assert match_row_to_acl("ip vpn-instance FW", rules)[0] is not None
    assert match_row_to_acl("ip vpn-instance _management_vpn_", rules)[0] is None


def test_service_config_is_owned_by_kind_on_every_leaf():
    # deleting a service must remove its config, so ownership cannot
    # come from the NetBox service list. leaf2 has no service and still owns these.
    from generators.firewall_bgp import FirewallBgp
    from generators.tenant_vrfs import TenantVrfs
    from generators.vlanifs import Vlanifs

    leaf = routed()["leaf2"]
    services, vrfs, bgp = acl(Vlanifs, leaf), acl(TenantVrfs, leaf), acl(FirewallBgp, leaf)
    assert match_row_to_acl("interface 10GE1/0/4", services)[0] is not None
    assert match_row_to_acl("interface Vlanif3000", services)[0] is not None
    assert match_row_to_acl("ip vpn-instance FW", vrfs)[0] is not None
    assert match_row_to_acl("bgp 65001", bgp)[1] is not None
    children = match_row_to_acl("bgp 65001", bgp)[1]
    assert match_row_to_acl("vpn-instance FW", children)[0] is not None
    # Never the bypass Vlanif (MlagBypass), segment instances (TenantBgp) or the management plane.
    assert match_row_to_acl("interface Vlanif4093", services)[0] is None
    assert match_row_to_acl("vpn-instance web-500", children)[0] is None
    assert match_row_to_acl("vpn-instance _management_vpn_", children)[0] is None
    assert match_row_to_acl("interface MEth0/0/0", services)[0] is None
    assert match_row_to_acl("ip vpn-instance _management_vpn_", vrfs)[0] is None


def test_deleting_a_service_removes_all_of_it():
    """The removal patch Annet builds, with every generator's ACL, from a leaf that has the
    FW service and routed segments to the same leaf after they are all deleted."""
    from tests.generators.harness import patch_to

    leaf1 = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=example_segments)}["leaf1"]
    lines = patch_to(render(routed()["leaf1"]), leaf1).splitlines()

    for removal in ("undo ip vpn-instance FW", "undo interface Vlanif3000", " undo vpn-instance FW",
                    " undo ipv4-family vpn-instance FW", " undo port link-type", "undo vlan batch 3000",
                    "undo ip vpn-instance web-500", "undo interface Vbdif500"):
        assert removal in lines, "\n".join(lines)
    assert lines.index(" undo ipv4-family vpn-instance FW") < lines.index(" undo vpn-instance FW")
    assert lines.index(" undo vpn-instance FW") < lines.index("undo ip vpn-instance FW")
    assert lines.index("undo interface Vlanif3000") < lines.index("undo ip vpn-instance FW")
    assert lines.index("undo interface Vlanif3000") < lines.index("undo vlan batch 3000")
    assert not any("4093" in line or "MEth" in line or "_management_vpn_" in line for line in lines)


def test_firewall_bgp_only_on_the_service_leaf():
    leaf1, leaf2 = render(routed()["leaf1"]), render(routed()["leaf2"])
    assert "  vpn-instance FW\n    peer 10.255.0.2 as-number 65100\n    peer 10.255.0.2 bfd enable\n" in leaf1
    assert "  ipv4-family vpn-instance FW\n    import-route static\n    peer 10.255.0.2 enable\n" in leaf1
    assert "vpn-instance FW" not in leaf2


def test_no_firewall_asn_no_bgp_session():
    device = routed()["leaf1"]
    service = device.config_context["services"]["FW"]
    saved = service.pop("asn")
    try:
        config = render(device)
        assert "ip vpn-instance FW\n" in config and "  vpn-instance FW\n" not in config
    finally:
        service["asn"] = saved


def test_m_lag_server_trunk_settings_are_owned_but_the_trunk_stays_mlags():
    """Vlanifs owns port link-type / allow-pass on M-LAG server trunks (services on a pair);
    Mlag still owns the trunk itself and alone may delete it."""
    from generators.mlag import Mlag
    from generators.vlanifs import Vlanifs

    leaf = routed()["leaf1"]
    trunk = match_row_to_acl("interface Eth-Trunk1", acl(Vlanifs, leaf))
    assert trunk[0] is not None and match_row_to_acl("port link-type trunk", trunk[1])[0] is not None
    assert match_row_to_acl("dfs-group 1 m-lag 1", trunk[1])[0] is None
    assert match_row_to_acl("interface Eth-Trunk1", acl(Mlag, leaf))[0] is not None
    assert match_row_to_acl("interface Eth-Trunk0", acl(Vlanifs, leaf))[0] is None   # the peer-link
