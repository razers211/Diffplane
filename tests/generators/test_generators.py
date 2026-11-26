import pytest
from annet.generators.exceptions import GeneratorError

from generators.fabric import MissingFabricSettings
from generators.snmp import community_alias
from tests.generators.harness import StubDevice, render

CONTEXT = {"fabric": {"ntp_servers": ["192.0.2.1"], "snmp": {}, "loopback_prefix": "10.0.0.0/24"}}


def test_breed_is_vrp85_for_lab_model():
    assert StubDevice("leaf1", "CE6881H-48S6CQ").breed == "vrp85"
    eos = StubDevice("leaf1", "vEOS-lab")   # Annet takes an Arista model as EOS
    assert eos.breed == "eos4" and eos.hw.vendor == "arista"


def test_missing_fabric_context_fails_instead_of_removing_ntp():
    with pytest.raises(GeneratorError) as excinfo:
        render(StubDevice("leaf1", "CE6881H-48S6CQ", {}))

    assert isinstance(excinfo.value.__cause__, MissingFabricSettings)


def test_snmp_location_and_contact_are_optional():
    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", CONTEXT))

    assert "sys-info location" not in config
    assert "sys-info contact" not in config
    assert "snmp-agent sys-info version v2c\nsnmp-agent sys-info version v3 disable\n" in config


def test_hostname_follows_device_name():
    assert "sysname border1\n" in render(StubDevice("border1", "CE6881H-48S6CQ", CONTEXT))


def test_snmp_community_from_environment(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")

    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", CONTEXT))

    assert f"snmp-agent community read ANNET-TEST alias {community_alias('ANNET-TEST')}\n" in config


def test_no_community_without_environment():
    assert "community" not in render(StubDevice("leaf1", "CE6881H-48S6CQ", CONTEXT))


def test_community_alias_is_stable_and_changes_with_the_community():
    assert community_alias("ANNET-TEST") == community_alias("ANNET-TEST")
    assert community_alias("ANNET-TEST") != community_alias("ANNET-TEST2")
    assert "ANNET" not in community_alias("ANNET-TEST")


def test_ntp_through_a_vpn_instance():
    context = {"fabric": {"ntp_servers": ["203.0.113.123"], "snmp": {}, "ntp_vpn_instance": "_management_vpn_",
                          "loopback_prefix": "10.10.0.0/24"}}

    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", context))

    assert "ntp unicast-server 203.0.113.123 vpn-instance _management_vpn_\n" in config


def test_a_fabric_without_ntp_servers_manages_no_ntp():
    """(east has none): no NTP lines generated, and none owned, so a
    hand-made server is left alone."""
    from tests.generators.harness import patch_to

    device = StubDevice("east-leaf1", "CE6881H-48S6CQ", {"fabric": {**CONTEXT["fabric"], "ntp_servers": []}})
    config = render(device)

    assert "ntp" not in config
    running = config + "ntp unicast-server 192.0.2.9\n"
    assert "ntp" not in patch_to(running, device)


def example_devices() -> dict:
    from tests.generators.harness import devices_from_fabric

    return {d.hostname: d for d in devices_from_fabric("tests/fixtures/example-small.yaml")}


def test_dfs_priority_lower_hostname_wins():
    from generators.fabric import dfs_priority

    devices = example_devices()

    assert dfs_priority(devices["leaf1"], "leaf2") == 150
    assert dfs_priority(devices["leaf2"], "leaf1") == 100
    assert "  priority" not in render(devices["leaf2"])   # 100 is the default, never displayed


def test_dfs_heartbeat_uses_both_loopbacks():
    config = render(example_devices()["leaf1"])

    assert "  dual-active detection source ip 10.10.0.2 peer 10.10.0.3\n" in config


def test_mlag_needs_the_dfs_key(monkeypatch):
    from generators.mlag import MissingDfsPassword

    monkeypatch.delenv("MLAG_DFS_PASSWORD")

    with pytest.raises(GeneratorError) as excinfo:
        render(example_devices()["leaf1"])
    assert isinstance(excinfo.value.__cause__, MissingDfsPassword)


def test_spines_get_no_mlag():
    config = render(example_devices()["spine1"])

    assert "dfs-group" not in config and "v-stp" not in config and "Eth-Trunk" not in config


def test_bypass_routes_point_at_each_other():
    devices = example_devices()
    leaf1, leaf2 = render(devices["leaf1"]), render(devices["leaf2"])

    assert "ip route-static 10.10.0.3 255.255.255.255 10.12.0.2 preference 1\n" in leaf1
    assert "ip route-static 10.10.0.2 255.255.255.255 10.12.0.1 preference 1\n" in leaf2
    assert "  reserved for vxlan bypass\n" in leaf1


def test_nve_mac_is_derived_from_the_vtep_address():
    import ipaddress

    from generators.nve import nve_mac

    assert nve_mac(ipaddress.IPv4Address("10.10.1.1")) == "0200-0a0a-0101"
    assert nve_mac(ipaddress.IPv4Address("10.0.1.2")) == "0200-0a00-0102"


def test_nve_is_shared_by_the_pair():
    devices = example_devices()
    nve1 = [l for l in render(devices["leaf1"]).splitlines() if l.startswith(("  source", "  mac-address"))]
    nve2 = [l for l in render(devices["leaf2"]).splitlines() if l.startswith(("  source", "  mac-address"))]

    assert nve1 == nve2 == ["  source 10.10.1.1", "  mac-address 0200-0a0a-0101"]
    assert "  pip-source 10.10.0.2 peer 10.10.0.3 bypass\n" in render(devices["leaf1"])
    assert "  pip-source 10.10.0.3 peer 10.10.0.2 bypass\n" in render(devices["leaf2"])


def test_leaf_peers_with_spines_and_spine_reflects_to_leaves():
    devices = example_devices()
    leaf, spine = render(devices["leaf1"]), render(devices["spine1"])

    assert "  peer 10.10.0.1 as-number 65001\n" in leaf and "reflect-client" not in leaf
    assert "    undo policy vpn-target\n" in leaf
    assert "    undo policy vpn-target\n" in spine
    for leaf_loopback in ("10.10.0.2", "10.10.0.3"):
        assert f"    peer {leaf_loopback} reflect-client\n" in spine


def test_bgp_needs_the_fabric_asn():
    devices = example_devices()
    device = devices["leaf1"]
    del device.config_context["fabric"]["asn"]

    with pytest.raises(GeneratorError):
        render(device)


def test_bgp_acl_does_not_own_other_bgp_lines():
    import textwrap

    from annet.annlib.rbparser.acl import compile_acl_text
    from annet.annlib.patching import match_row_to_acl

    from generators.bgp_evpn import BgpEvpn

    device = example_devices()["leaf1"]
    rules = compile_acl_text(textwrap.dedent(BgpEvpn(device.storage).acl_huawei(device)), "huawei")

    assert match_row_to_acl("bgp 65001", rules)[0] is not None
    # Seen on leaf1 (V300): must never be removed by the BGP generator.
    assert match_row_to_acl("bgp yang-mode enable", rules)[0] is None


def test_unknown_segment_on_a_port_fails_generation():
    from generators.fabric import UnknownSegment

    device = example_devices()["leaf1"]
    port = next(i for i in device.interfaces if i.name == "10GE1/0/3")
    port.custom_fields = {"segments": [{"vni": 999, "vid": None}]}

    with pytest.raises(GeneratorError) as excinfo:
        render(device)
    assert isinstance(excinfo.value.__cause__, UnknownSegment)


def test_no_segments_no_bridge_domains():
    config = render(example_devices()["leaf1"])

    assert "bridge-domain" not in config and " mode l2" not in config


def test_a_segment_name_that_cannot_be_a_vpn_instance_stops_generation():
    """a routed segment's VPN instance is <name>-<bd>; never a broken line on a switch."""
    from types import SimpleNamespace

    import pytest

    from generators.fabric import BadSegmentName, routed_segments

    def device(name):
        return SimpleNamespace(config_context={"segments": {"10500": {
            "bd_id": 500, "name": name, "routing": {"gateway": "10.100.0.1/24"}}}})

    assert routed_segments(device("web"))[10500].vrf == "web-500"
    for bad in ("web tier", "a" * 28):
        with pytest.raises(BadSegmentName):
            routed_segments(device(bad))


def test_ospf_authentication_writes_the_keychain_and_each_link_names_it(monkeypatch):
    """leaf1's console forms; a key comes only from OSPF_KEYS."""
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")
    context = {"fabric": {**CONTEXT["fabric"], "ospf_auth": {"site": "example", "enabled": True, "keys": [1, 2], "send": 2}}}
    monkeypatch.setenv("OSPF_KEYS", '{"example": {"1": "ANNET-KEY-1", "2": "ANNET-KEY-2"}}')
    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", context))
    assert ("keychain fabric-ospf mode absolute\n"
            "  key-id 1\n    algorithm hmac-sha-256\n    key-string cipher ANNET-KEY-1\n"
            "    receive-time 00:00 2020-01-01 duration infinite\n"
            "  key-id 2\n    algorithm hmac-sha-256\n    key-string cipher ANNET-KEY-2\n"
            "    receive-time 00:00 2020-01-01 duration infinite\n    default send-key-id\n") in config
    monkeypatch.setenv("OSPF_KEYS", "{}")
    with pytest.raises(GeneratorError, match="OspfKeychain"):
        render(StubDevice("leaf1", "CE6881H-48S6CQ", context))


def test_switch_login_writes_the_stored_forms_and_never_the_default_domain(monkeypatch):
    """leaf1's stored forms; a key comes only from RADIUS_KEYS."""
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")
    login = {"site": "example", "enabled": True, "domain": "radius", "servers": ["192.0.2.1"]}
    context = {"fabric": {**CONTEXT["fabric"], "switch_login": login}}
    monkeypatch.setenv("RADIUS_KEYS", '{"example": "ANNET-KEY"}')
    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", context))
    assert ("radius-server template fab-radius\n  radius-server user-name domain-included\n"
            "  radius-server shared-key cipher ANNET-KEY\n"
            "  radius-server accounting 192.0.2.1 1813 vpn-instance _management_vpn_ weight 80\n"
            "  radius-server authentication 192.0.2.1 1812 vpn-instance _management_vpn_ weight 80\n") in config
    assert "  domain radius\n    authentication-scheme fab-radius\n" in config
    assert "domain default" not in config and "local-user" not in config
    monkeypatch.setenv("RADIUS_KEYS", "{}")
    with pytest.raises(GeneratorError, match="SwitchLogin"):
        render(StubDevice("leaf1", "CE6881H-48S6CQ", context))


def snmp_context(**snmp) -> dict:
    return {"fabric": {**CONTEXT["fabric"], "snmp": snmp}}


def test_a_v3_reader_adds_v3_its_group_user_and_trap_target(monkeypatch):
    """the forms leaf1 stored; never the password lines."""
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")
    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", snmp_context(
        readers={"nms": {"auth": "sha2-256", "priv": "aes256"}},
        traps={"NMS-1": {"address": "192.0.2.10", "reader": "nms"}, "OLD": {"address": "192.0.2.11", "reader": "gone"}})))
    assert "snmp-agent sys-info version v2c v3\n" in config and "v3 disable" not in config
    assert "snmp-agent community read ANNET-TEST" in config
    assert ("snmp-agent group v3 FABRIC-RO privacy notify-view ViewDefault\n"
            "snmp-agent usm-user v3 nms\nsnmp-agent usm-user v3 nms group FABRIC-RO\n"
            "snmp-agent target-host host-name NMS-1 trap address udp-domain 192.0.2.10 vpn-instance _management_vpn_ "
            "params securityname nms v3 privacy\n"
            "snmp-agent protocol source-interface MEth0/0/0\n"
            "snmp-agent protocol vpn-instance _management_vpn_\n") in config
    assert "OLD" not in config and "authentication-mode" not in config


def test_v3_only_drops_the_community(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")
    config = render(StubDevice("leaf1", "CE6881H-48S6CQ", snmp_context(v2c=False, readers={"nms": {}})))
    assert "snmp-agent sys-info version v3\n" in config and "community" not in config


def test_the_acl_owns_only_the_platform_s_users_and_targets():
    from generators.snmp import Snmp
    acl = Snmp(None).acl_huawei(StubDevice("leaf1", "CE6881H-48S6CQ", snmp_context(
        owned={"users": ["nms"], "traps": ["NMS-1"]})))
    assert "snmp-agent usm-user v3 nms$" in acl and "snmp-agent usm-user v3 nms group ~" in acl
    assert "authentication-mode" not in acl and "snmp-agent target-host host-name NMS-1 ~" in acl
