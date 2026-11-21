import asyncio
import ipaddress
from pathlib import Path

import pytest
import yaml

from seed.loader import Seeder, SeedError
from seed.model import load, parse
from tests.seed.fake_netbox import FakeNetBox

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"


def seed(netbox: FakeNetBox):
    return asyncio.run(Seeder(netbox, load(EXAMPLE)).run())


@pytest.fixture
def netbox() -> FakeNetBox:
    nb = FakeNetBox()
    seed(nb)
    return nb


def by_name(nb: FakeNetBox, endpoint: str, key: str = "name") -> dict:
    return {o[key]: o for o in nb.objects.get(endpoint, {}).values()}


def iface(nb: FakeNetBox, device: str, name: str) -> dict:
    device_id = by_name(nb, "dcim/devices")[device]["id"]
    return next(i for i in nb.objects["dcim/interfaces"].values() if i["device"] == device_id and i["name"] == name)


def ips_on(nb: FakeNetBox, device: str, name: str) -> list[dict]:
    iface_id = iface(nb, device, name)["id"]
    return [ip for ip in nb.objects["ipam/ip-addresses"].values() if ip["assigned_object_id"] == iface_id]


def test_second_run_changes_nothing(netbox):
    writes = netbox.writes

    report = seed(netbox)

    assert report.changes == []
    assert netbox.writes == writes


def test_devices_roles_and_types(netbox):
    devices = by_name(netbox, "dcim/devices")
    roles = {o["id"]: o["slug"] for o in netbox.objects["dcim/device-roles"].values()}

    assert {n: roles[d["role"]] for n, d in devices.items()} == {
        "spine1": "spine", "spine2": "spine",
        "leaf1": "leaf", "leaf2": "leaf", "leaf3": "leaf", "leaf4": "leaf",
    }
    assert set(roles.values()) == {"spine", "leaf", "border-leaf"}
    (device_type,) = netbox.objects["dcim/device-types"].values()
    (manufacturer,) = netbox.objects["dcim/manufacturers"].values()
    assert device_type["model"] == "CE6881H-48S6CQ"
    assert manufacturer["name"] == "Huawei"


def test_custom_fields_and_tag(netbox):
    fields = by_name(netbox, "extras/custom-fields")

    assert fields["mlag_peer"]["related_object_type"] == "dcim.device"
    assert fields["mlag_peer"]["object_types"] == ["dcim.device"]
    assert fields["bd_id"]["object_types"] == ["vpn.l2vpn"]
    assert fields["ssh_host_keys"]["object_types"] == ["dcim.device"] and fields["ssh_host_keys"]["type"] == "json"
    assert fields["ztp"]["object_types"] == ["dcim.device"] and fields["ztp"]["type"] == "json"
    assert set(by_name(netbox, "extras/tags", "slug")) == {"peer-link", "m-lag", "unnumbered"}


def test_site_has_fabric_asn(netbox):
    (site,) = netbox.objects["dcim/sites"].values()
    (asn,) = netbox.objects["ipam/asns"].values()

    assert asn["asn"] == 65001
    assert site["asns"] == [asn["id"]]


def test_mlag_peers_are_mutual(netbox):
    devices = by_name(netbox, "dcim/devices")

    for a, b in [("leaf1", "leaf2"), ("leaf3", "leaf4")]:
        assert devices[a]["custom_fields"]["mlag_peer"] == devices[b]["id"]
        assert devices[b]["custom_fields"]["mlag_peer"] == devices[a]["id"]
    assert "mlag_peer" not in devices["spine1"]["custom_fields"]


def test_peer_link_trunk_members_and_cables(netbox):
    tag_id = by_name(netbox, "extras/tags", "slug")["peer-link"]["id"]

    for leaf in ["leaf1", "leaf2", "leaf3", "leaf4"]:
        trunk = iface(netbox, leaf, "Eth-Trunk0")
        assert trunk["type"] == "lag"
        assert trunk["tags"] == [tag_id]
        for member in ["100GE1/0/5", "100GE1/0/6"]:
            assert iface(netbox, leaf, member)["lag"] == trunk["id"]
    for member in ["100GE1/0/5", "100GE1/0/6"]:
        assert iface(netbox, "leaf1", member)["cable"] == iface(netbox, "leaf2", member)["cable"] is not None


def test_cable_count(netbox):
    # 8 spine-leaf links + 2 peer-link members on each of 2 pairs.
    assert len(netbox.objects["dcim/cables"]) == 12


def test_loopback0_unique_host_routes_in_pool(netbox):
    pool = ipaddress.ip_network("10.0.0.0/24")
    addresses = []
    for device in ["spine1", "spine2", "leaf1", "leaf2", "leaf3", "leaf4"]:
        (ip,) = ips_on(netbox, device, "LoopBack0")
        assert ip["role"] == "loopback"
        assert ip["address"].endswith("/32")
        addresses.append(ipaddress.ip_interface(ip["address"]).ip)
    assert len(set(addresses)) == 6
    assert all(a in pool for a in addresses)


def test_vtep_shared_within_pair_and_distinct_between_pairs(netbox):
    def vtep(leaf):
        (ip,) = ips_on(netbox, leaf, "LoopBack1")
        assert ip["role"] == "anycast"
        return ip["address"]

    assert vtep("leaf1") == vtep("leaf2")
    assert vtep("leaf3") == vtep("leaf4")
    assert vtep("leaf1") != vtep("leaf3")
    assert ipaddress.ip_interface(vtep("leaf1")) in ipaddress.ip_network("10.0.1.0/24")
    assert vtep("leaf1").endswith("/32")
    spine_ifaces = {i["name"] for i in netbox.objects["dcim/interfaces"].values()
                    if i["device"] == by_name(netbox, "dcim/devices")["spine1"]["id"]}
    assert "LoopBack1" not in spine_ifaces


def test_spine_links_share_a_slash30(netbox):
    pool = ipaddress.ip_network("10.1.0.0/24")
    for spine, spine_port, leaf, leaf_port in [
        ("spine1", "100GE1/0/1", "leaf1", "100GE1/0/1"),
        ("spine2", "100GE1/0/4", "leaf4", "100GE1/0/2"),
    ]:
        (a,) = ips_on(netbox, spine, spine_port)
        (b,) = ips_on(netbox, leaf, leaf_port)
        net_a = ipaddress.ip_interface(a["address"]).network
        assert net_a == ipaddress.ip_interface(b["address"]).network
        assert net_a.prefixlen == 30 and net_a.subnet_of(pool)
        assert a["address"] != b["address"]
    children = [p for p in netbox.objects["ipam/prefixes"].values()
                if p["prefix"].endswith("/30") and ipaddress.ip_network(p["prefix"]).subnet_of(pool)]
    assert len(children) == 8
    assert len({p["prefix"] for p in children}) == 8


def test_recovers_half_finished_vtep(netbox):
    leaf2_vtep = ips_on(netbox, "leaf2", "LoopBack1")[0]
    expected = leaf2_vtep["address"]
    del netbox.objects["ipam/ip-addresses"][leaf2_vtep["id"]]

    report = seed(netbox)

    assert report.changes == [f"create ipam/ip-addresses {expected} for leaf2 LoopBack1 (VTEP)"]
    assert ips_on(netbox, "leaf2", "LoopBack1")[0]["address"] == expected


def test_recovers_half_finished_link(netbox):
    (leaf_ip,) = ips_on(netbox, "leaf1", "100GE1/0/1")
    (spine_ip,) = ips_on(netbox, "spine1", "100GE1/0/1")
    del netbox.objects["ipam/ip-addresses"][leaf_ip["id"]]
    prefixes_before = len(netbox.objects["ipam/prefixes"])

    report = seed(netbox)

    (new_ip,) = ips_on(netbox, "leaf1", "100GE1/0/1")
    assert len(report.changes) == 1
    assert len(netbox.objects["ipam/prefixes"]) == prefixes_before
    assert ipaddress.ip_interface(new_ip["address"]).network == ipaddress.ip_interface(spine_ip["address"]).network


def test_fixes_loopback_mask(netbox):
    (ip,) = ips_on(netbox, "spine1", "LoopBack0")
    ip["address"] = ip["address"].replace("/32", "/24")

    report = seed(netbox)

    assert report.changes == ["update ipam/ip-addresses spine1 LoopBack0: address"]
    assert ip["id"] in netbox.objects["ipam/ip-addresses"]
    assert ips_on(netbox, "spine1", "LoopBack0")[0]["address"].endswith("/32")


def test_restores_changed_attribute(netbox):
    iface(netbox, "leaf3", "100GE1/0/6")["lag"] = None

    report = seed(netbox)

    assert report.changes == ["update dcim/interfaces leaf3 100GE1/0/6: lag"]


def test_refuses_to_rewire_conflicting_cable(netbox):
    a = iface(netbox, "spine1", "100GE1/0/1")
    other = iface(netbox, "spine1", "100GE1/0/2")
    a["cable"] = other["cable"]

    with pytest.raises(SeedError, match="existing cable"):
        seed(netbox)


def test_fabric_config_context_on_site(netbox):
    (site,) = netbox.objects["dcim/sites"].values()
    (context,) = netbox.objects["extras/config-contexts"].values()

    assert context["name"] == "fabric-example"
    assert context["sites"] == [site["id"]]
    assert context["data"] == {
        "fabric": {
            "ntp_servers": ["192.0.2.1", "192.0.2.2"],
            "snmp": {"location": "example-dc", "contact": "noc@example.com"},
            "mlag": {"bypass_vlan": 4093},
            "gateway_mac": "0000-5e00-0101",
            "architecture": "symmetric",
            "asn": 65001,
            "loopback_prefix": "10.0.0.0/24",   # BgpEvpn tells fabric peers by it
        }
    }


def test_config_context_data_is_replaced_not_merged(netbox):
    (context,) = netbox.objects["extras/config-contexts"].values()
    context["data"]["fabric"]["stale"] = True

    report = seed(netbox)

    assert report.changes == ["update extras/config-contexts fabric-example: data"]
    (context,) = netbox.objects["extras/config-contexts"].values()
    assert "stale" not in context["data"]["fabric"]


def seed_active(netbox: FakeNetBox):
    """Seed the small example, then make leaf1 active as the GUI does."""
    from tests.helpers import seed as seed_and_activate

    small = Path(__file__).resolve().parents[1] / "fixtures" / "example-small.yaml"
    return asyncio.run(seed_and_activate(netbox, load(small)))


def test_example_devices_are_planned(netbox):
    assert {d["status"] for d in netbox.objects["dcim/devices"].values()} == {"planned"}


def test_the_seed_creates_devices_planned_and_keeps_the_guis_status():
    """the GUI owns status and management addresses from the start."""
    from orchestrator.lifecycle import Lifecycle

    nb = FakeNetBox()
    asyncio.run(Seeder(nb, load(EXAMPLE)).run())
    assert {d["status"] for d in nb.objects["dcim/devices"].values()} == {"planned"}
    assert not any(i["name"].startswith("MEth") and ips_on(nb, "leaf1", i["name"])
                   for i in nb.objects["dcim/interfaces"].values())

    asyncio.run(Lifecycle(nb).activate("leaf1", "172.24.0.11/24"))
    devices = by_name(nb, "dcim/devices")
    assert devices["leaf1"]["status"] == "active"
    meth = iface(nb, "leaf1", "MEth0/0/0")
    assert meth["mgmt_only"] is True and meth["type"] == "1000base-t"
    (ip,) = ips_on(nb, "leaf1", "MEth0/0/0")
    assert ip["address"] == "172.24.0.11/24" and devices["leaf1"]["primary_ip4"] == ip["id"]
    # Re-seeding changes nothing: it never writes status or management addresses.
    assert asyncio.run(Seeder(nb, load(EXAMPLE)).run()).changes == []
    assert by_name(nb, "dcim/devices")["leaf1"]["status"] == "active"


def test_two_fabrics_coexist(netbox):
    asyncio.run(Seeder(netbox, parse(second_dc())).run())

    assert seed(netbox).changes == []
    assert asyncio.run(Seeder(netbox, parse(second_dc())).run()).changes == []
    assert len(netbox.objects["dcim/sites"]) == 2


def second_dc() -> dict:
    """The example fabric again, as another DC: same prefixes, VLANs and ASN, other names."""
    from tests.helpers import dc2_data

    return dc2_data()


def test_two_fabrics_can_reuse_the_same_prefixes():
    nb = FakeNetBox()
    seed_active(nb)

    report = asyncio.run(Seeder(nb, parse(second_dc())).run())

    assert report.changes
    example_lo = ips_on(nb, "leaf1", "LoopBack0")[0]
    pip_lo = ips_on(nb, "dc2-leaf1", "LoopBack0")[0]
    assert example_lo["address"] == pip_lo["address"] and example_lo["vrf"] != pip_lo["vrf"]
    assert {v["name"] for v in nb.objects["ipam/vrfs"].values()} == {"fabric-example", "fabric-dc2"}
    assert seed_active(nb).changes == []
    assert asyncio.run(Seeder(nb, parse(second_dc())).run()).changes == []


def test_management_addresses_stay_global():
    nb = FakeNetBox()
    seed_active(nb)

    (mgmt,) = ips_on(nb, "leaf1", "MEth0/0/0")
    (loopback,) = ips_on(nb, "leaf1", "LoopBack0")

    assert mgmt.get("vrf") is None and loopback["vrf"] is not None


def test_device_names_are_unique_across_fabrics():
    nb = FakeNetBox()
    seed_active(nb)
    data = second_dc()
    data["spines"][0]["name"] = "spine1"
    data["links"] = [l for l in data["links"] if l["spine"] != "dc2-spine1"] + [
        {**l, "spine": "spine1"} for l in data["links"] if l["spine"] == "dc2-spine1"]

    with pytest.raises(SeedError, match="already used by another fabric"):
        asyncio.run(Seeder(nb, parse(data)).run())


def test_global_data_from_before_fabric_vrfs_is_adopted():
    nb = FakeNetBox()
    seed_active(nb)
    vrf_id = next(v["id"] for v in nb.objects["ipam/vrfs"].values())
    for obj in list(nb.objects["ipam/prefixes"].values()) + list(nb.objects["ipam/ip-addresses"].values()):
        if obj.get("vrf") == vrf_id:
            obj["vrf"] = None   # as seeded before this change
    del nb.objects["ipam/vrfs"][vrf_id]

    report = seed_active(nb)

    assert any(c.startswith("move 10.10.0.0/24") for c in report.changes)
    assert all(p["vrf"] is not None for p in nb.objects["ipam/prefixes"].values())
    assert ips_on(nb, "leaf1", "MEth0/0/0")[0].get("vrf") is None
    assert seed_active(nb).changes == []


def test_mlag_trunks_on_both_leaves(netbox):
    mlag = by_name(netbox, "extras/tags", "slug")["m-lag"]["id"]
    for leaf in ["leaf1", "leaf2"]:
        trunk = iface(netbox, leaf, "Eth-Trunk1")
        assert trunk["type"] == "lag" and trunk["tags"] == [mlag]   # an M-LAG trunk
        assert iface(netbox, leaf, "10GE1/0/1")["lag"] == trunk["id"]
    assert not any(i["name"] == "Eth-Trunk1" and i["device"] == by_name(netbox, "dcim/devices")["leaf3"]["id"]
                   for i in netbox.objects["dcim/interfaces"].values())


def test_mlag_bypass_vlanif_shares_a_slash30_per_pair(netbox):
    pool = ipaddress.ip_network("10.2.0.0/24")
    nets = []
    for a, b in [("leaf1", "leaf2"), ("leaf3", "leaf4")]:
        (ip_a,) = ips_on(netbox, a, "Vlanif4093")
        (ip_b,) = ips_on(netbox, b, "Vlanif4093")
        net = ipaddress.ip_interface(ip_a["address"]).network
        assert net == ipaddress.ip_interface(ip_b["address"]).network and net.prefixlen == 30
        assert net.subnet_of(pool) and ip_a["address"] != ip_b["address"]
        nets.append(net)
    assert nets[0] != nets[1]


def test_changing_the_bypass_vlan_renames_the_vlanif_and_keeps_its_address(netbox):
    data = yaml.safe_load(EXAMPLE.read_text())
    before = ips_on(netbox, "leaf1", "Vlanif4093")[0]["address"]
    data["fabric"]["mlag"]["bypass_vlan"] = 3000

    report = asyncio.run(Seeder(netbox, parse(data)).run())

    assert "update dcim/interfaces leaf1 Vlanif4093: name -> Vlanif3000" in report.changes
    assert ips_on(netbox, "leaf1", "Vlanif3000")[0]["address"] == before
    assert not any(i["name"] == "Vlanif4093" for i in netbox.objects["dcim/interfaces"].values())
    assert asyncio.run(Seeder(netbox, parse(data)).run()).changes == []


def test_every_front_panel_port_exists(netbox):
    leaf1 = by_name(netbox, "dcim/devices")["leaf1"]["id"]
    names = {i["name"] for i in netbox.objects["dcim/interfaces"].values() if i["device"] == leaf1}

    assert {f"10GE1/0/{n}" for n in range(1, 49)} | {f"100GE1/0/{n}" for n in range(1, 7)} <= names
    assert iface(netbox, "leaf1", "10GE1/0/48")["type"] == "10gbase-x-sfpp"
