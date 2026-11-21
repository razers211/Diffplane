"""A switch's control plane for the GUI: peers named after the switch that owns their address,
planned partners expected down, problems listed for "Needs you"."""

import asyncio

import pytest

from orchestrator.controlplane import control_plane, fabric_problems, owners, shape
from orchestrator.prometheus import PrometheusError
from tests.collector.test_collector import example_netbox


def run(coro):
    return asyncio.run(coro)


def example():
    nb = example_netbox()
    leaf = run(nb.find_one("dcim/devices", name="leaf1"))
    run(nb.update("dcim/devices", leaf["id"], {"status": "active"}))
    return nb


def series(peer: str, state: str = "active", heartbeat: float = 0) -> list:
    peer_labels = {"device": "leaf1", "vpn": "_public_", "peer": peer, "family": "evpn"}
    return [
        ({"__name__": "fabric_bgp_peer_info", **peer_labels, "state": state, "remote_as": "65001"}, 0, 1),
        ({"__name__": "fabric_bgp_peer_state_seconds", **peer_labels}, 0, 600),
        ({"__name__": "fabric_bgp_peer_prefixes", **peer_labels, "kind": "received"}, 0, 0),
        ({"__name__": "fabric_mlag_dfs_info", "device": "leaf1", "role": "local", "state": "master",
          "causation": "PEERLINKDOWN"}, 0, 1),
        ({"__name__": "fabric_mlag_heartbeat_up", "device": "leaf1"}, 0, heartbeat),
        ({"__name__": "fabric_mlag_peer_link_up", "device": "leaf1", "interface": "Eth-Trunk0"}, 0, 0),
        ({"__name__": "fabric_routes", "device": "leaf1", "vpn": "_public_", "protocol": "direct", "kind": "total"}, 0, 6),
        ({"__name__": "fabric_routes", "device": "leaf1", "vpn": "_public_", "protocol": "rip", "kind": "total"}, 0, 0),
    ]


def spine_address(nb) -> str:
    owner_of, _ = run(owners(nb, "example"))
    return next(ip for ip, d in owner_of.items() if d["name"] == "spine1")


def test_the_labs_planned_spine_and_peer_leaf_are_expected_down():
    nb = example()
    owner_of, devices = run(owners(nb, "example"))
    view = shape("leaf1", series(spine_address(nb)), owner_of, devices)
    peer = view["bgp"][0]
    assert peer["switch"] == "spine1" and peer["expected_down"] and peer["seconds"] == 600
    assert view["mlag"]["partner"] == "leaf2" and view["mlag"]["expected_down"] and view["mlag"]["heartbeat"] is False
    assert view["routes"] == {"_public_": {"direct": {"total": 6}}}       # empty protocols left out
    assert view["problems"] == []


def test_peers_down_towards_active_switches_are_problems():
    nb = example()
    spine = run(nb.find_one("dcim/devices", name="spine1"))
    run(nb.update("dcim/devices", spine["id"], {"status": "active"}))
    leaf2 = run(nb.find_one("dcim/devices", name="leaf2"))
    run(nb.update("dcim/devices", leaf2["id"], {"status": "active"}))
    owner_of, devices = run(owners(nb, "example"))
    view = shape("leaf1", series(spine_address(nb)), owner_of, devices)
    assert view["problems"][0].startswith(f"BGP peer {spine_address(nb)} (spine1), evpn: active")
    assert "M-LAG heartbeat with leaf2 lost (PEERLINKDOWN)" in view["problems"]
    assert shape("leaf1", series(spine_address(nb), "established", 1), owner_of, devices)["problems"] == []


class Prom:
    def __init__(self, data):
        self.data, self.asked = data, []

    async def query(self, expr):
        self.asked.append(expr)
        return self.data


def test_one_query_per_switch_and_names_are_checked():
    nb = example()
    prom = Prom(series(spine_address(nb)))
    view = run(control_plane(nb, prom, "leaf1"))
    assert view["collected"] and prom.asked == ['{__name__=~"fabric_(bgp_peer_.*|ospf_neighbor_info|routes|mlag_.*|'
                                                'vxlan_tunnel_up|mac_entries|control_query_refused)",device="leaf1"}']
    with pytest.raises(PrometheusError, match="invalid device name"):
        run(control_plane(nb, prom, 'x"} or vector(1'))
    assert run(fabric_problems(nb, prom, "example")) == []                  # the example: everything down is expected
