"""Why can't A reach B on synthetic tables: each failure kind, both ways, both architectures,
IPv4 and IPv6."""

import pytest

from orchestrator import reach, trace
from tests.orchestrator.test_trace import TABLES, fabric

WEB, DB = "10.100.0.10", "10.100.1.20"


def rib(*routes):
    return [{"vpn": v, "prefix": p, "protocol": "direct", "active": True} for v, p in routes]


def ribs():
    """Symmetric: leaf1 has web's instance only, leaf3 db's only (and the routes between them)."""
    web, db = rib(("web-500", "10.100.0.0/24"), ("web-500", "10.100.1.0/24")), rib(("db-600", "10.100.1.0/24"), ("db-600", "10.100.0.0/24"))
    return {"leaf1": web, "leaf3": db, "spine1": web + db}


def facts(**over):
    up = {"leaf1": {"10GE1/0/3": True}, "leaf3": {"10GE1/0/7": True, "10GE1/0/8": True}}
    return reach.Facts(**{"ports_up": up, "ribs": ribs(), **over})


def groups(mode="enforce", contracts=(), **web):
    return {"mode": mode, "contracts": list(contracts),
            "epgs": {"1": {"segment": 10500, **web}, "2": {"segment": 10600}}}


ROUTED = {10500: {"name": "web"}, 10600: {"name": "db"}}
TCP5432 = {"from": 1, "to": 2, "services": [{"protocol": "tcp", "ports": [5432, 5432]}]}


def test_a_working_path_reaches_there_and_back():
    r = reach.reach(fabric(), TABLES, facts(), WEB, DB)
    assert r["verdict"] == "reaches" and r["stop"] is None
    assert {c["direction"] for c in r["checks"]} == {"there", "back"}
    assert r["there"]["result"] == r["back"]["result"] == trace.DELIVERED


def test_the_source_port_down_is_the_first_stop():
    f = facts(ports_up={"leaf1": {"10GE1/0/3": False}, "leaf3": {"10GE1/0/7": True}})
    r = reach.reach(fabric(), TABLES, f, WEB, DB)
    assert r["verdict"] == "blocked" and r["stop"]["switch"] == "leaf1" and r["stop"]["check"] == "port"
    assert r["stop"]["page"] == "#/switches/leaf1"


def test_port_protection_says_so_and_points_at_its_page():
    r = reach.reach(fabric(), TABLES, facts(trips={("leaf3", "10GE1/0/7"): ["storm-control"]}), WEB, DB)
    assert r["stop"]["check"] == "protection" and "storm-control" in r["sentence"]
    assert r["stop"]["page"] == "#/network/protection"


def test_a_route_missing_on_the_switch_is_not_deployed():
    r = reach.reach(fabric(), TABLES, facts(ribs={**ribs(), "leaf1": rib(("web-500", "10.100.0.0/24"))}), WEB, DB)
    assert r["stop"]["check"] == "route" and "not deployed, or out of sync" in r["sentence"]
    assert r["stop"]["switch"] == "leaf1" and r["stop"]["page"] == "#/changes"


def test_an_inactive_route_does_not_count_but_a_more_specific_one_does():
    routes = [{**rib(("web-500", "10.100.1.0/24"))[0], "active": False}]
    r = reach.reach(fabric(), TABLES, facts(ribs={**ribs(), "leaf1": routes}), WEB, DB)
    assert r["stop"]["check"] == "route"
    routes = rib(("web-500", "10.100.1.20/32"), ("web-500", "10.100.0.0/24"))
    assert reach.reach(fabric(), TABLES, facts(ribs={**ribs(), "leaf1": routes}), WEB, DB)["verdict"] == "reaches"


def test_the_far_instance_is_checked_on_the_far_leaf_not_the_near_one():
    r = reach.reach(fabric(), TABLES, facts(), WEB, DB)
    assert [(c["switch"], c["ok"]) for c in r["checks"] if c["check"] == "route" and c["direction"] == "there"] == \
        [("leaf1", True), ("leaf3", True)]


def test_a_table_not_read_is_not_a_stop():
    r = reach.reach(fabric(), TABLES, facts(ribs={}), WEB, DB)
    assert r["stop"] is None and r["verdict"] == "unknown"


def test_no_route_in_intent_stops_on_the_segments_page():
    r = reach.reach(fabric(), TABLES, facts(), DB, "10.100.2.9")
    assert r["stop"]["check"] == "path" and r["stop"]["page"] == "#/network/segments"


def test_enforce_without_a_contract_blocks_on_the_security_page():
    r = reach.reach(fabric(), TABLES, facts(microseg=groups(), routed=ROUTED), WEB, DB, 6, 5432)
    assert r["stop"]["check"] == "security" and r["stop"]["page"] == "#/network/security"
    assert r["sentence"] == "Blocked at leaf1: no rule lets web reach db on TCP 5432"


def test_a_contract_opens_its_port_and_no_other():
    f = facts(microseg=groups(contracts=[TCP5432]), routed=ROUTED)
    assert reach.reach(fabric(), TABLES, f, WEB, DB, 6, 5432)["checks"][-1]["ok"] is not False
    there = [c for c in reach.reach(fabric(), TABLES, f, WEB, DB, 6, 5432)["checks"] if c["check"] == "security"]
    assert there[0]["ok"] is True
    assert reach.reach(fabric(), TABLES, f, WEB, DB, 6, 80)["stop"]["check"] == "security"


def test_the_way_back_is_checked_too():
    # web -> db is allowed; db -> web has no contract of its own, so a query from db stops at once
    f = facts(microseg=groups(contracts=[TCP5432]), routed=ROUTED)
    r = reach.reach(fabric(), TABLES, f, DB, WEB, 6, 5432)
    assert r["stop"]["check"] == "security" and r["stop"]["direction"] == "there"


def test_learn_only_says_what_enforce_would_do():
    r = reach.reach(fabric(), TABLES, facts(microseg=groups("learn"), routed=ROUTED), WEB, DB, 6, 5432)
    assert r["stop"] is None and any("Enforce would block" in c["text"] for c in r["checks"])


def test_a_rule_the_switch_could_not_install_blocks():
    f = facts(microseg=groups(contracts=[TCP5432]), routed=ROUTED, failed_rules={"leaf1": {"GBP-1-2"}})
    assert "not installed on leaf1" in reach.reach(fabric(), TABLES, f, WEB, DB, 6, 5432)["sentence"]


def test_a_named_group_by_prefix_wins_over_its_segment():
    doc = groups(contracts=[])
    doc["epgs"]["3"] = {"segment": 10500, "name": "admins", "prefixes": ["10.100.0.0/28"], "macs": [], "inside": "deny"}
    doc["contracts"] = [{"from": 3, "to": 2, "services": [{"protocol": "any"}]}]
    assert reach.reach(fabric(), TABLES, facts(microseg=doc, routed=ROUTED), WEB, DB)["stop"]["check"] == "security"
    doc["epgs"]["3"]["prefixes"] = ["10.100.0.8/29"]
    r = reach.reach(fabric(), TABLES, facts(microseg=doc, routed=ROUTED), WEB, DB)
    assert not any(c["check"] == "security" and c["direction"] == "there" and c["ok"] is False for c in r["checks"])


def test_inside_deny_stops_two_hosts_of_a_segment():
    t = trace.Tables(arp=TABLES.arp, macs={**TABLES.macs})
    r = reach.reach(fabric(), t, facts(microseg=groups(inside="deny"), routed=ROUTED), WEB, "cccc-0000-0030")
    assert r["stop"]["check"] == "security" and "inside web" in r["sentence"]


def test_a_firewall_ends_the_path_and_is_said():
    r = reach.reach(fabric(), TABLES, facts(ribs={**ribs(), "leaf1": rib(("web-500", "0.0.0.0/0"), ("web-500", "10.100.0.0/24"))}), WEB, "8.8.8.8")
    assert r["verdict"] == "leaves" and "firewall" in r["sentence"]


def test_a_stretched_segment_is_said_when_the_host_is_not_here():
    r = reach.reach(fabric(), TABLES, facts(stretched={10500: "dc2"}), WEB, "10.100.0.99")
    assert r["verdict"] == "unknown" and "stretched to dc2" in r["sentence"]


def test_centralized_routes_on_the_gateway_pair():
    r = reach.reach(fabric("centralized"), TABLES, facts(), WEB, DB)
    assert r["verdict"] == "reaches" and any(c["check"] == "route" and c["switch"] == "spine1" for c in r["checks"])
    missing = {**ribs(), "spine1": rib(("web-500", "10.100.0.0/24"))}
    assert reach.reach(fabric("centralized"), TABLES, facts(ribs=missing), WEB, DB)["stop"]["switch"] == "spine1"


def v6_fabric():
    f = fabric()
    for s, net in zip(f.model.segments, ("fd00:a::1/64", "fd00:b::1/64")):
        s["routing"]["ipv6"] = {"gateway": net}
    return f


V6 = trace.Tables(arp={"leaf1": [{"vpn": "web-500", "ip": "FD00:A::10", "mac": "aaaa-0000-0010", "type": "nd", "interface": "Vbdif500"}],
                       "leaf3": [{"vpn": "db-600", "ip": "fd00:b::20", "mac": "bbbb-0000-0020", "type": "nd", "interface": "Vbdif600"}]},
                  macs=TABLES.macs)


def test_ipv6_both_ways_and_a_missing_route():
    routes = rib(("web-500", "FD00:B::/64"), ("web-500", "fd00:a::/64"), ("db-600", "fd00:a::/64"), ("db-600", "fd00:b::/64"))
    f = {"leaf1": routes, "leaf3": routes}   # also: an address written in capitals by the switch
    r = reach.reach(v6_fabric(), V6, facts(ribs=f), "fd00:a::10", "fd00:b::20")
    assert r["verdict"] == "reaches" and r["there"]["result"] == trace.DELIVERED
    r = reach.reach(v6_fabric(), V6, facts(ribs={**f, "leaf1": routes[1:]}), "fd00:a::10", "fd00:b::20")
    assert r["stop"]["check"] == "route"


def test_ipv6_to_a_segment_without_it_is_a_drop():
    r = reach.reach(fabric(), TABLES, facts(), WEB, "fd00:b::20")
    assert r["verdict"] == "blocked"


def test_an_imported_fabrics_own_vpn_instance_names_are_used():
    f = fabric()
    for s in f.model.segments:
        s["vrf"] = s["name"].upper()
    t = trace.Tables(arp={"leaf1": [{**TABLES.arp["leaf1"][0], "vpn": "WEB"}], "leaf3": [{**TABLES.arp["leaf3"][0], "vpn": "DB"}]},
                     macs=TABLES.macs)
    routes = {"leaf1": rib(("WEB", "10.100.1.0/24"), ("WEB", "10.100.0.0/24")), "leaf3": rib(("DB", "10.100.1.0/24"), ("DB", "10.100.0.0/24"))}
    r = reach.reach(f, t, facts(ribs=routes), WEB, DB)
    assert r["verdict"] == "reaches" and r["there"]["source"]["port"] == "10GE1/0/3.1"


def test_nd_entries_join_the_arp_list():
    ns = trace.ND_NS
    reply = (f'<data><ipv6-nd xmlns="{ns}"><ipv6-neighbors><ipv6-neighbor><if-name>Vbdif500</if-name><ipv6-addr>fd00:a::10</ipv6-addr>'
             '<mac-addr>aaaa-0000-0010</mac-addr><vpn-name>web-500</vpn-name></ipv6-neighbor></ipv6-neighbors></ipv6-nd></data>')
    arp, _ = trace.parse_tables(reply)
    assert arp == [{"vpn": "web-500", "ip": "fd00:a::10", "mac": "aaaa-0000-0010", "type": "nd", "interface": "Vbdif500"}]


def test_known_hosts_leave_the_management_network_out():
    t = trace.Tables(arp={**TABLES.arp, "leaf2": [{"vpn": "_management_vpn_", "ip": "10.150.99.1", "mac": "x", "type": "dynamic-arp"}]},
                     macs={**TABLES.macs, "leaf4": [{"bd": 110, "mac": "dddd-0000-0001", "interface": "10GE1/0/1", "peer": None}]})
    found = {h["value"]: h["kind"] for h in reach.known_hosts(fabric(), t)}
    assert found[WEB] == "host" and found["10.100.0.1"] == "gateway" and found["aaaa-0000-0010"] == "mac"
    assert "10.150.99.1" not in found and "dddd-0000-0001" not in found


@pytest.mark.parametrize("proto,number", [(None, None), ("any", None), ("tcp", 6), ("47", 47)])
def test_protocols(proto, number):
    assert reach.protocol_number(proto) == number


def test_a_bad_protocol_is_refused():
    with pytest.raises(ValueError):
        reach.protocol_number("gre")


def test_a_switch_as_the_source_reaches_and_is_checked_on_its_own_table():
    from tests.orchestrator.test_trace import with_switches

    r = reach.reach(with_switches(fabric()), TABLES, facts(), "leaf1/web", DB)
    assert r["verdict"] == "reaches" and r["there"]["source"]["switch"] == "leaf1"
    missing = {**ribs(), "leaf1": rib(("web-500", "10.100.0.0/24"))}
    assert reach.reach(with_switches(fabric()), TABLES, facts(ribs=missing), "leaf1/web", DB)["stop"]["switch"] == "leaf1"
