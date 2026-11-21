"""Auto-import of hand-made routing config."""

import asyncio
import ipaddress

from orchestrator.routing_import import (Exclusions, RoutingImporter, describe, drop_dangling, merged, new_objects,
                                         routing_objects)
from orchestrator.routing_intent import DeviceRouting, RoutingStore
from tests.collector.test_collector import example_netbox

# A running config as `annet show current` prints it (VRP: one-space indents, '#', '!' lines).
RUNNING = """\
!Software Version V300R023C00SPC500
#
sysname leaf1
#
dfs-group 1
 authentication-mode hmac-sha256 password %+%##!!!!SECRET-DFS-KEY!!!!%+%#
 priority 150
#
ip vpn-instance FW
 ipv4-family
  route-distinguisher 10.10.0.2:3000
#
ip ip-prefix HAND-PFX index 10 permit 10.50.0.0 16 greater-equal 16 less-equal 24
ip ip-prefix HAND-PFX index 20 deny 0.0.0.0 0 less-equal 32
ip as-path-filter HAND-ASP index 10 permit ^64999$
ip community-filter basic HAND-COMM index 10 permit 65001:200
#
route-policy HAND-IN permit node 10
 if-match ip-prefix HAND-PFX
 apply local-preference 150
#
route-policy HAND-IN deny node 20
#
route-policy HAND-COST permit node 10
 apply cost 50
#
ip route-static 0.0.0.0 0.0.0.0 vpn-instance _management_vpn_ 172.24.0.1
ip route-static vpn-instance _management_vpn_ 0.0.0.0 0.0.0.0 172.24.0.1
ip route-static 10.10.0.3 255.255.255.255 10.12.0.2 preference 1
ip route-static 10.60.0.0 255.255.0.0 NULL0
ip route-static 10.61.0.0 255.255.0.0 192.0.2.254 preference 20
ip route-static 10.62.0.0 255.255.0.0 Vlanif3000
ip route-static vpn-instance FW 10.70.0.0 255.255.0.0 NULL0
ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500
ip route-static vpn-instance OTHER 10.71.0.0 255.255.0.0 NULL0
#
bgp 65001
 router-id 10.10.0.2
 peer 10.10.0.1 as-number 65001
 peer 10.10.0.1 connect-interface LoopBack0
 peer 192.0.2.1 as-number 64999
 peer 192.0.2.2 as-number 64998
 peer 192.0.2.2 password cipher %^%#SECRET-PEER-PASSWORD%^%#
 ipv4-family unicast
  aggregate 10.50.0.0 255.255.0.0 detail-suppressed
  peer 192.0.2.1 enable
  peer 192.0.2.1 route-policy HAND-IN import
  peer 192.0.2.1 ip-prefix HAND-PFX export
  peer 192.0.2.2 enable
 l2vpn-family evpn
  undo policy vpn-target
  peer 10.10.0.1 enable
 vpn-instance FW
  peer 10.255.0.2 as-number 65100
  peer 10.255.0.2 bfd enable
  peer 192.0.2.30 as-number 64700
 vpn-instance OTHER
  peer 192.0.2.40 as-number 64800
 ipv4-family vpn-instance FW
  import-route static
  aggregate 10.70.0.0 255.255.0.0 suppress-policy HAND-COST
  peer 10.255.0.2 enable
  peer 192.0.2.30 enable
  peer 192.0.2.30 route-policy HAND-COST import
#
ospf 1 router-id 10.10.0.2
 area 0.0.0.0
#
ospf 2 router-id 192.0.2.10 vpn-instance FW
 area 0.0.0.0
#
ospf 3 router-id 192.0.2.11
 area 0.0.0.0
#
return
"""

EXCLUSIONS = Exclusions(loopback_pool=ipaddress.IPv4Network("10.10.0.0/24"),
                        firewall_next_hops={"FW": ["10.255.0.2"]}, platform_vpns={"FW"})


def result():
    return routing_objects(RUNNING, EXCLUSIONS)


def test_supported_hand_made_objects_are_found():
    routing = result().routing
    assert routing.prefix_lists["HAND-PFX"][1].le == 32
    assert routing.as_path_filters["HAND-ASP"][0].regex == "^64999$"
    assert routing.community_filters["HAND-COMM"][0].community == "65001:200"
    assert [n.node for n in routing.route_policies["HAND-IN"]] == [10, 20]
    assert {(s.vpn, str(s.prefix), str(s.next_hop), s.preference) for s in routing.static_routes} == {
        (None, "10.60.0.0/16", "NULL0", None), (None, "10.61.0.0/16", "192.0.2.254", 20),
        ("FW", "10.70.0.0/16", "NULL0", None)}
    peers = {(p.vpn, str(p.address)): p for p in routing.bgp_peers}
    assert set(peers) == {(None, "192.0.2.1"), ("FW", "192.0.2.30")}   # 192.0.2.30 is dropped later (HAND-COST)
    assert peers[(None, "192.0.2.1")].import_policy == "HAND-IN"
    assert peers[(None, "192.0.2.1")].export_prefix_list == "HAND-PFX"
    assert [(a.vpn, str(a.prefix)) for a in routing.aggregates] == [(None, "10.50.0.0/16"), ("FW", "10.70.0.0/16")]
    assert [(o.process, o.vpn) for o in routing.ospf] == [(2, "FW")]


def test_what_the_platform_owns_otherwise_is_never_imported():
    text = " ".join(describe(result().routing))
    for owned in ("10.10.0.1", "10.10.0.3", "10.255.0.2", "_management_vpn_", "web-500", "OTHER", "ospf 1 "):
        assert owned not in text, owned


def test_unsupported_forms_are_reported_by_identity_never_by_line():
    unmanaged = result().unmanaged
    assert "route-policy HAND-COST (a clause not supported)" in unmanaged
    assert "BGP peer 192.0.2.2 (an option not supported)" in unmanaged
    assert any(u.startswith("static route 10.62.0.0 255.255.0.0") for u in unmanaged)
    assert "ospf 3 (form not supported)" in unmanaged
    assert "SECRET" not in " ".join(unmanaged)


def test_objects_using_what_was_not_imported_are_left_out():
    found = result()
    new, dropped = drop_dangling(found.routing, DeviceRouting())
    # HAND-COST was not importable, so what uses it is left out too.
    assert "aggregate 10.70.0.0/16 (uses HAND-COST, not imported)" in dropped
    assert "BGP peer 192.0.2.30 in FW (uses HAND-COST, not imported)" in dropped
    assert all(a.suppress_policy is None for a in new.aggregates)
    assert [str(p.address) for p in new.bgp_peers] == ["192.0.2.1"]
    merged(DeviceRouting(), new).check_references()


def test_intent_wins_and_retired_objects_are_never_reimported():
    found = result().routing
    intent = DeviceRouting.model_validate({"prefix_lists": {"HAND-PFX": [
        {"index": 10, "action": "permit", "prefix": "10.50.0.0/16", "ge": 16, "le": 20}]}})
    retired = DeviceRouting.model_validate({"static_routes": [{"prefix": "10.60.0.0/16", "next_hop": "NULL0"}]})

    new = new_objects(found, intent, retired)

    assert "HAND-PFX" not in new.prefix_lists             # intent's version stays
    assert all(str(s.prefix) != "10.60.0.0/16" for s in new.static_routes)


class FakeAnnet:
    def __init__(self, text):
        self.text = text
        self.reads = 0

    async def current(self, device):
        self.reads += 1
        return self.text


def importer_on_lab(text=RUNNING):
    from orchestrator.routing import Routing

    nb = example_netbox()
    asyncio.run(Routing(nb).create_service("example", "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2",
                                           65100))
    return nb, RoutingImporter(nb, FakeAnnet(text))


def test_import_adopts_once_and_is_idempotent():
    nb, importer = importer_on_lab()

    first = asyncio.run(importer.run("leaf1"))
    intent = asyncio.run(RoutingStore(nb).load("example")).device("leaf1+leaf2")
    second = asyncio.run(importer.run("leaf1"))

    assert first.error is None and "peer 192.0.2.1" in first.imported and "ospf 2 in FW" in first.imported
    assert "HAND-IN" in intent.route_policies and intent.ospf[0].vpn == "FW"
    assert second.imported == [] and second.error is None
    assert importer.reports["leaf1"] is second


def test_exclusions_follow_the_fabric():
    nb, importer = importer_on_lab()
    exclusions = asyncio.run(importer.exclusions("example", "leaf1"))

    assert exclusions.loopback_pool == ipaddress.IPv4Network("10.10.0.0/24")
    assert exclusions.firewall_next_hops == {"FW": ["10.255.0.2"]} and exclusions.platform_vpns == {"FW"}


def test_a_failing_import_changes_nothing_and_says_why():
    class Broken(FakeAnnet):
        async def current(self, device):
            raise RuntimeError("cannot reach leaf1")

    nb, _ = importer_on_lab()
    report = asyncio.run(RoutingImporter(nb, Broken("")).run("leaf1"))

    assert report.imported == [] and "cannot reach leaf1" in report.error
    assert asyncio.run(RoutingStore(nb).load("example")).device("leaf1+leaf2").is_empty()


# -- M-LAG pairs -------------------------------------------------------

class PairAnnet:
    def __init__(self, texts: dict):
        self.texts = texts

    async def current(self, device):
        text = self.texts[device]
        if isinstance(text, Exception):
            raise text
        return text


def pair_importer(texts):
    nb, _ = importer_on_lab()
    leaf2 = asyncio.run(nb.find_one("dcim/devices", name="leaf2"))
    asyncio.run(nb.update("dcim/devices", leaf2["id"], {"status": "active"}))   # the peer is built
    return nb, RoutingImporter(nb, PairAnnet(texts))


def test_a_pair_adopts_only_what_both_leaves_have_identically():
    leaf2 = RUNNING.replace("ip route-static 10.61.0.0 255.255.0.0 192.0.2.254 preference 20\n", "")
    nb, importer = pair_importer({"leaf1": RUNNING, "leaf2": leaf2})

    report = asyncio.run(importer.run("leaf1"))
    intent = asyncio.run(RoutingStore(nb).load("example")).device("leaf1+leaf2")

    assert report.error is None and "peer 192.0.2.1" in report.imported
    assert {str(s.prefix) for s in intent.static_routes} == {"10.60.0.0/16"}
    assert any("10.61.0.0/16" in u and "differs" in u for u in report.unmanaged)
    # FW exists on leaf1 only (its service): its objects differ between the leaves.
    assert all(s.vpn is None for s in intent.static_routes) and intent.ospf == []


def test_an_unreachable_peer_stops_the_import_for_the_pair():
    nb, importer = pair_importer({"leaf1": RUNNING, "leaf2": TimeoutError("no answer")})

    report = asyncio.run(importer.run("leaf1"))

    assert report.imported == [] and "leaf2 cannot be read" in report.error
    assert asyncio.run(RoutingStore(nb).load("example")).device("leaf1+leaf2").is_empty()


def test_a_long_import_error_keeps_its_last_lines():
    """Annet's traceback says why at the end; the report must not stop mid-traceback."""
    from orchestrator.routing_import import clip

    traceback = "AnnetError: annet show failed: Traceback\n" + "  File \"x.py\", line 1\n" * 200 + "OSError: the real reason"
    kept = clip(traceback)
    assert len(kept) <= 1200 and kept.startswith("AnnetError") and kept.endswith("OSError: the real reason")
    assert clip("short") == "short"


RUNNING6 = """
#
ip ipv6-prefix HAND6 index 10 permit FD00:100:: 48 greater-equal 48 less-equal 64
#
route-policy POL6 permit node 10
 apply local-preference 200
 if-match ipv6 address prefix-list HAND6
#
ipv6 route-static FD00:300:: 48 NULL0
ipv6 route-static vpn-instance FW FD00:301:: 48 FD00:255::9
ipv6 route-static vpn-instance FW FD12:3456:789A:1:: 64 vpn-instance web-500
#
bgp 65001
 peer FD00:99::2 as-number 65300
 vpn-instance FW
  peer FD00:255::4 as-number 65600
  peer FD00:255::9 as-number 65301
 ipv6-family unicast
  aggregate FD00:100:: 48 detail-suppressed
  peer FD00:99::2 enable
  peer FD00:99::2 route-policy POL6 import
  peer FD00:99::2 ipv6-prefix HAND6 export
 ipv6-family vpn-instance FW
  peer FD00:255::4 enable
  peer FD00:255::9 enable
#
return
"""


def test_hand_made_ipv6_objects_are_found_and_the_firewall_session_is_not():
    """the IPv6 forms verified on leaf1."""
    exclusions = Exclusions(loopback_pool=ipaddress.IPv4Network("10.10.0.0/24"), firewall_next_hops={"FW": ["10.255.0.2"]},
                            firewall_next_hops6={"FW": ["fd00:255::4"]}, platform_vpns={"FW"})
    found = routing_objects(RUNNING6, exclusions)
    routing = found.routing
    assert found.unmanaged == []
    assert routing.prefix_lists["HAND6"][0].prefix == ipaddress.ip_network("fd00:100::/48")
    assert routing.route_policies["POL6"][0].match_ip_prefix == "HAND6"
    assert sorted(str(r.prefix) for r in routing.static_routes) == ["fd00:300::/48", "fd00:301::/48"]   # inter-VPN skipped
    assert {(p.vpn, str(p.address)) for p in routing.bgp_peers} == {(None, "fd00:99::2"), ("FW", "fd00:255::9")}
    peer = next(p for p in routing.bgp_peers if p.vpn is None)
    assert (peer.import_policy, peer.export_prefix_list) == ("POL6", "HAND6")
    assert [str(a.prefix) for a in routing.aggregates] == ["fd00:100::/48"]
    routing.check_references()


def test_ipv6_objects_in_an_instance_without_ipv6_are_left_alone():
    exclusions = Exclusions(loopback_pool=ipaddress.IPv4Network("10.10.0.0/24"), platform_vpns={"FW"},
                            platform_vpns6=set())
    found = routing_objects(RUNNING6, exclusions)
    assert {(p.vpn, str(p.address)) for p in found.routing.bgp_peers} == {(None, "fd00:99::2")}
    assert [str(r.prefix) for r in found.routing.static_routes] == ["fd00:300::/48"]
    assert any("FD00:255::9 in FW" in u for u in found.unmanaged)
