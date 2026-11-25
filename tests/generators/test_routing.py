"""General routing generators: filters, policies, statics, peers, aggregates, OSPF.

The intent uses the forms verified on leaf1, in platform VPN instances of the
example tenant (service FW, segment web-500) instead of the throwaway TEST-VRF.
"""

from functools import cache
from pathlib import Path

import pytest

from tests.generators.harness import devices_from_fabric, patch_to, render
from tests.generators.test_golden import example_routing

EXAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml"

INTENT = {
    "prefix_lists": {"TEST-PFX": [{"index": 10, "action": "permit", "prefix": "10.200.0.0/16", "ge": 24, "le": 24}]},
    "as_path_filters": {"TEST-ASP": [{"index": 10, "action": "permit", "regex": "^65100$"}]},
    "community_filters": {"TEST-COMM": [{"index": 10, "action": "permit", "community": "65001:100"}]},
    "route_policies": {"TEST-IN": [
        {"node": 10, "action": "permit", "match_ip_prefix": "TEST-PFX", "apply_local_preference": 200},
        {"node": 20, "action": "deny"},
    ]},
    "static_routes": [{"prefix": "10.201.0.0/24", "next_hop": "NULL0"},
                      {"vpn": "FW", "prefix": "10.202.0.0/24", "next_hop": "NULL0"}],
    "bgp_peers": [
        {"address": "192.0.2.1", "asn": 65100, "import_policy": "TEST-IN", "export_prefix_list": "TEST-PFX"},
        {"vpn": "FW", "address": "192.0.2.5", "asn": 65200, "import_policy": "TEST-IN"},
        {"vpn": "web-500", "address": "192.0.2.6", "asn": 65300},
    ],
    "aggregates": [{"prefix": "10.200.0.0/16", "detail_suppressed": True},
                   {"vpn": "FW", "prefix": "10.202.0.0/16", "suppress_policy": "TEST-IN"}],
    "ospf": [{"process": 2, "vpn": "FW", "router_id": "192.0.2.10", "areas": ["0.0.0.0"]}],
}


def with_intent(intent):
    async def prepare(nb):
        from orchestrator.routing_intent import DeviceRouting, RoutingStore

        await example_routing(nb)
        store = RoutingStore(nb)
        await store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(INTENT))
        if intent is not INTENT:
            await store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(intent))
    return prepare


@cache
def leaf(which: str):
    intent = {"full": INTENT, "none": {}}[which]
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=with_intent(intent))}["leaf1"]


@cache
def tenant_only():
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=example_routing)}["leaf1"]


def test_every_verified_form_is_generated():
    """Each line survives its own generator's ACL (render filters by it), in VRP's form."""
    config = render(leaf("full"))
    for line in (
        "ip ip-prefix TEST-PFX index 10 permit 10.200.0.0 16 greater-equal 24 less-equal 24\n",
        "ip as-path-filter TEST-ASP index 10 permit ^65100$\n",
        "ip community-filter basic TEST-COMM index 10 permit 65001:100\n",
        "route-policy TEST-IN deny node 20\n",
        "ip route-static 10.201.0.0 255.255.255.0 NULL0\n",
        "ip route-static vpn-instance FW 10.202.0.0 255.255.255.0 NULL0\n",
        "  peer 192.0.2.1 as-number 65100\n",
        "    peer 192.0.2.1 enable\n",
        "    peer 192.0.2.1 route-policy TEST-IN import\n",
        "    peer 192.0.2.1 ip-prefix TEST-PFX export\n",
        "    aggregate 10.200.0.0 255.255.0.0 detail-suppressed\n",
        "    peer 192.0.2.5 as-number 65200\n",
        "    aggregate 10.202.0.0 255.255.0.0 suppress-policy TEST-IN\n",
        "    peer 192.0.2.6 as-number 65300\n",
        "ospf 2 router-id 192.0.2.10 vpn-instance FW\n  area 0.0.0.0\n",
    ):
        assert line in config, line
    # Inside a node Annet orders the lines its own way; the diff compares contents.
    assert block(config, "route-policy TEST-IN permit node 10") == {"if-match ip-prefix TEST-PFX",
                                                                  "apply local-preference 200"}


def block(config: str, header: str) -> set[str]:
    lines = config.splitlines()
    start = lines.index(header) + 1
    children = []
    for line in lines[start:]:
        if not line.startswith(" "):
            break
        children.append(line.strip())
    return set(children)


def test_tenant_config_is_unchanged_by_routing_intent():
    full, tenant = render(leaf("full")), render(tenant_only())
    for line in tenant.splitlines():
        assert line in full.splitlines(), line


def test_adding_orders_filters_before_policies_before_bgp():
    lines = patch_to(render(tenant_only()), leaf("full")).splitlines()

    def at(text):
        return next(i for i, line in enumerate(lines) if line.strip().startswith(text))

    assert at("ip ip-prefix TEST-PFX") < at("route-policy TEST-IN permit node 10") < at("bgp 65001")
    assert at("peer 192.0.2.5 as-number 65200") < at("peer 192.0.2.5 enable")


def test_removing_intent_retires_and_removes_everything_in_reverse():
    lines = patch_to(render(leaf("full")), leaf("none")).splitlines()
    text = "\n".join(lines)
    for removal in ("undo ip ip-prefix TEST-PFX", "undo ip as-path-filter TEST-ASP", "undo ip community-filter",
                    "undo route-policy TEST-IN", "undo ip route-static 10.201.0.0 255.255.255.0 NULL0",
                    "undo ip route-static vpn-instance FW 10.202.0.0 255.255.255.0 NULL0",
                    "undo peer 192.0.2.1", "undo peer 192.0.2.5", "undo peer 192.0.2.6",
                    "undo aggregate 10.200.0.0 255.255.0.0", "undo ospf 2"):
        assert removal in text, f"{removal}\n{text}"
    first_bgp = next(i for i, line in enumerate(lines) if "undo peer" in line)
    assert first_bgp < next(i for i, line in enumerate(lines) if line.startswith("undo route-policy"))
    # The tenant stays: removing routing intent touches no container.
    assert "undo ip vpn-instance" not in text and "undo vpn-instance" not in text


def test_hand_made_routing_objects_stay_after_the_retired_ones_are_cleared():
    """Once a verified deploy cleared `retired`, the same lines on a switch are someone's
    hand-made config: never removed (they are imported instead)."""
    import copy

    cleared = copy.copy(leaf("none"))
    cleared.config_context = copy.deepcopy(cleared.config_context)
    cleared.config_context["routing"].pop("retired", None)
    assert "undo" not in patch_to(render(leaf("full")), cleared)


def test_a_vpn_instance_the_platform_does_not_create_is_an_error():
    """The orchestrator refuses such intent on save (phase 7); the generator still fails if a
    document holding it reaches NetBox another way."""
    from annet.generators.exceptions import GeneratorError

    bad = dict(INTENT, static_routes=[{"vpn": "NOPE", "prefix": "10.1.0.0/24", "next_hop": "NULL0"}])

    async def prepare(nb):
        from orchestrator.routing_intent import DeviceRouting, RoutingDocument, RoutingStore
        from orchestrator.segments import SegmentError

        await example_routing(nb)
        store = RoutingStore(nb)
        with pytest.raises(SegmentError, match="VPN instance NOPE is not on leaf1"):
            await store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(bad))
        await store.save("example", RoutingDocument.model_validate({"devices": {"leaf1+leaf2": bad}}))

    device = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=prepare)}["leaf1"]
    with pytest.raises(GeneratorError) as error:
        render(device)
    assert "NOPE" in f"{error.value} {error.value.__cause__}"


def test_mlag_bypass_no_longer_owns_every_host_route():
    from annet.annlib.patching import match_row_to_acl
    from annet.annlib.rbparser.acl import compile_acl_text

    from generators.mlag_bypass import MlagBypass

    device = tenant_only()
    rules = compile_acl_text(MlagBypass(device.storage).acl_huawei(device), "huawei")
    assert match_row_to_acl("ip route-static 10.10.0.2 255.255.255.255 10.12.0.2 preference 1", rules)[0] is not None
    assert match_row_to_acl("ip route-static 192.0.2.99 255.255.255.255 NULL0", rules)[0] is None


async def with_isp(nb):
    """The example tenant plus an ISP service on leaf1, its session a routing-intent peer."""
    from orchestrator.routing import Routing
    from orchestrator.routing_intent import DeviceRouting, RoutingStore

    await example_routing(nb)
    await Routing(nb).create_service("example", "ISP-A", "leaf1", "10GE1/0/11", 3002, "10.255.1.1/30", "10.255.1.2",
                                     kind="isp")
    await RoutingStore(nb).set_device("example", "leaf1+leaf2", DeviceRouting.model_validate({
        "prefix_lists": {"OUT": [{"index": 10, "action": "permit", "prefix": "10.100.0.0/16", "le": 24}]},
        "route_policies": {"TO-ISP": [{"node": 10, "action": "permit", "match_ip_prefix": "OUT"},
                                      {"node": 20, "action": "deny"}]},
        "bgp_peers": [{"vpn": "ISP-A", "address": "10.255.1.2", "asn": 64600, "export_policy": "TO-ISP"}],
    }))


def test_an_isp_service_is_a_container_and_its_session_comes_from_intent():
    leaf = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=with_isp)}["leaf1"]
    config = render(leaf)

    for line in ("ip vpn-instance ISP-A\n", "interface Vlanif3002\n  ip binding vpn-instance ISP-A\n",
                 "    peer 10.255.1.2 as-number 64600\n", "    peer 10.255.1.2 route-policy TO-ISP export\n"):
        assert line in config, line
    lines = config.splitlines()
    start = lines.index("  ipv4-family vpn-instance ISP-A") + 1
    family = []
    for line in lines[start:]:
        if not line.startswith("    "):
            break
        family.append(line.strip())
    assert "peer 10.255.1.2 enable" in family
    assert "import-route static" not in family        # nothing advertised to an ISP without a policy
    assert "ip route-static vpn-instance ISP-A" not in config
    assert "peer 10.255.1.2 bfd enable" not in config  # that is the firewall's automatic session


def test_less_equal_alone_is_written_as_vrp_displays_it():
    """V300 stores `... 16 less-equal 24` as `... 16 greater-equal 16 less-equal 24` (leaf1)."""
    leaf = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=with_isp)}["leaf1"]
    assert "ip ip-prefix OUT index 10 permit 10.100.0.0 16 greater-equal 16 less-equal 24\n" in render(leaf)


# -- a service on an M-LAG pair -----------------------------------------

async def with_pair_firewall(nb):
    """A firewall on the pair's server trunk Eth-Trunk1, the default route of a routed segment."""
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments

    segments, routing = Segments(nb), Routing(nb)
    await segments.create("example", "web", 10500, 500)
    await segments.attach("example", "leaf1", "10GE1/0/3", 10500, None)
    await routing.create_service("example", "FW", "leaf1", "Eth-Trunk1", 3005, "10.100.105.0/29", "10.100.105.6", 65100)
    await routing.set_routing("example", 10500, True)


@cache
def pair_firewall():
    return {d.hostname: render(d) for d in devices_from_fabric(EXAMPLE, prepare=with_pair_firewall)}


def test_a_pair_service_is_the_dual_active_gateway_on_both_leaves():
    for leaf, main in (("leaf1", "10.100.105.1"), ("leaf2", "10.100.105.2")):
        config = pair_firewall()[leaf]
        assert ("interface Vlanif3005\n"
                "  ip binding vpn-instance FW\n"
                f"  ip address {main} 255.255.255.248\n"
                "  mac-address 0201-0a64-6903\n"
                "  m-lag ip address 10.100.105.3 255.255.255.248\n") in config, leaf
        assert "  port link-type trunk\n  port trunk allow-pass vlan 3005\n" in config
        assert "ip vpn-instance FW\n" in config


def test_a_pair_service_routes_identically_on_both_leaves():
    def routing(config):
        return sorted(line.strip() for line in config.splitlines()
                      if line.startswith("ip route-static vpn-instance") or "peer 10.100.105.6" in line
                      or line.strip() == "import-route static")

    leaf1, leaf2 = (routing(pair_firewall()[leaf]) for leaf in ("leaf1", "leaf2"))
    assert leaf1 == leaf2
    assert "ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.100.105.6" in leaf1
    assert "peer 10.100.105.6 as-number 65100" in leaf1 and "peer 10.100.105.6 bfd enable" in leaf1


def test_a_pair_service_is_removed_with_its_trunk_settings():
    before = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=with_pair_firewall)}["leaf1"]
    after = {d.hostname: d for d in devices_from_fabric(EXAMPLE)}["leaf1"]
    lines = [line.strip() for line in patch_to(render(before), after).splitlines()]

    assert "undo interface Vlanif3005" in lines and "undo ip vpn-instance FW" in lines
    assert any("undo port trunk allow-pass vlan" in line for line in lines)
    assert not any("undo interface Eth-Trunk1" in line or "dfs-group" in line for line in lines)  # the trunk stays


async def with_consecutive_services(nb):
    """Two firewall services on leaf1 with consecutive VLANs."""
    from orchestrator.routing import Routing

    routing = Routing(nb)
    await routing.create_service("example", "FWA", "leaf1", "10GE1/0/4", 3005, "10.255.0.1/30", "10.255.0.2")
    await routing.create_service("example", "FWB", "leaf1", "10GE1/0/5", 3006, "10.255.1.1/30", "10.255.1.2")


def test_consecutive_vlans_are_written_as_vrp_displays_them():
    """leaf1: VRP shows 3005 3006 as `3005 to 3006`; the list form left a diff."""
    leaf = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=with_consecutive_services)}["leaf1"]
    config = render(leaf)

    assert "vlan batch 3005 to 3006 4093\n" in config
    # What the switch displays after the deploy gives an empty diff.
    assert patch_to(config, leaf).strip() == ""

    bare = {d.hostname: d for d in devices_from_fabric(EXAMPLE)}["leaf1"]
    lines = [line.strip() for line in patch_to(config, bare).splitlines()]
    assert "undo interface Vlanif3005" in lines and "undo interface Vlanif3006" in lines
    assert any(line.startswith("undo vlan batch") and "3005" in line for line in lines), lines


def test_vlan_list():
    from generators.fabric import vlan_list

    assert vlan_list([4093, 3006, 3005]) == "3005 to 3006 4093"
    assert vlan_list([1, 2, 3, 5, 7, 8]) == "1 to 3 5 7 to 8"
    assert vlan_list([10]) == "10"


def own_routing(leaf1_peer: bool):
    """The pair's routing plus each switch's own peer, then leaf1's moved to leaf2."""
    async def prepare(nb):
        from orchestrator.routing_intent import DeviceRouting, RoutingStore

        await example_routing(nb)
        store = RoutingStore(nb)
        pfx = {"OWN": [{"index": 10, "action": "permit", "prefix": "198.51.100.0/24"}]}
        await store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(
            {"prefix_lists": pfx, "bgp_peers": [{"address": "192.0.2.1", "asn": 65100}]}))
        peer = {"address": "192.0.2.5", "asn": 65200, "export_prefix_list": "OWN"}
        await store.set_device("example", "leaf1" if leaf1_peer else "leaf2",
                               DeviceRouting.model_validate({"prefix_lists": pfx, "bgp_peers": [peer]}))
        if not leaf1_peer:   # it was leaf1's first: retired there
            await store.set_device("example", "leaf1", DeviceRouting.model_validate(
                {"prefix_lists": pfx, "bgp_peers": [peer]}))
            await store.set_device("example", "leaf1", DeviceRouting())
            await store.set_device("example", "leaf2", DeviceRouting.model_validate({"prefix_lists": pfx, "bgp_peers": [peer]}))
    return prepare


@cache
def own(leaf1_peer: bool):
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=own_routing(leaf1_peer))}


def test_a_switchs_own_peer_is_on_it_alone_beside_the_pairs():
    config = render(own(True)["leaf1"])
    assert "peer 192.0.2.1 as-number 65100" in config and "peer 192.0.2.5 as-number 65200" in config
    assert config.count("ip ip-prefix OWN index 10 permit 198.51.100.0 24") == 1   # identical in both: once
    assert patch_to(config, own(True)["leaf1"]) == ""


def test_moving_a_peer_to_the_other_switch_removes_it_from_the_first():
    before = render(own(True)["leaf1"])
    after = own(False)["leaf1"]
    lines = [line.strip() for line in patch_to(before, after).splitlines()]
    assert "undo peer 192.0.2.5" in lines, lines
    assert not any("192.0.2.1" in line for line in lines)   # the pair's peer stays
    assert "undo ip ip-prefix OWN index 10" not in " ".join(lines)   # still the pair's
