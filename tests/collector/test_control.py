"""Control-plane state: parsed from leaf1's recorded replies (probe), plus
synthetic replies for what the example cannot show (an OSPF neighbour, a VXLAN tunnel, an established peer)."""

import asyncio
from pathlib import Path

from ncclient.operations import RPCError
from prometheus_client.parser import text_string_to_metric_families

import collector.__main__ as main
from collector import control
from collector.metrics import FabricCollector
from tests.collector.test_collector import FakeSession, exposition

FIXTURES = Path(__file__).parent / "fixtures"


def value(text: str, name: str, **labels) -> float | None:
    """A sample's value from an exposition, matched on its whole label set (any label order)."""
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == name and sample.labels == labels:
                return sample.value
    return None


def recorded(name: str) -> str:
    return (FIXTURES / f"m31-{name}.xml").read_text()


EXAMPLE = {"instances": None, "mlag": recorded("mlag-states"), "tunnels": recorded("nvo3-tunnels"), "macs": recorded("mac-statistics")}


def test_leaf1_bgp_peers_and_route_counts():
    state = control.ControlState()
    control.parse_instances(recorded("bgp-peer-states"), state)
    assert [(p.instance, p.address, p.family, p.state, p.remote_as) for p in state.bgp] == [
        ("_public_", "10.10.0.1", "ipv4uni", "active", "65001"), ("_public_", "10.10.0.1", "evpn", "active", "65001")]
    assert state.bgp[0].seconds == 168 * 3600 + 37 * 60 and state.bgp[0].received == 0
    control.parse_instances(recorded("route-statistics"), state)
    assert state.routes[("_public_", "direct")] == (6, 6) and state.routes[("_public_", "ospf")] == (2, 0)


def test_leaf1_mlag_tunnels_and_macs():
    state = control.parse(EXAMPLE)
    assert state.dfs == {"role": "local", "heartbeat": "lost", "state": "master", "causation": "PEERLINKDOWN"}
    assert state.peer_links == {"Eth-Trunk0": False} and state.mlag_ports == {"Eth-Trunk1": False, "Eth-Trunk25": False}
    assert state.tunnels == [] and state.macs["total"] == 0 and state.macs["capacity"] == 98304
    assert state.missing == {"instances"}


NI, OSPF, NVO3, BGP = control.NI, control.OSPF, control.NVO3, control.BGP
SYNTHETIC_INSTANCES = f"""<data><network-instance xmlns="{NI}"><instances><instance><name>_public_</name>
<bgp xmlns="{BGP}"><base-process><peers><peer><address>10.10.0.1</address><afs><af><type>evpn</type><state>
<current-state>established</current-state><remote-as>65001</remote-as><up-down-duration>02d03h</up-down-duration>
<receive-prefix>40</receive-prefix><receive-active-prefix>38</receive-active-prefix><advertise-prefix>12</advertise-prefix>
</state></af></afs></peer></peers></base-process></bgp>
<ospfv2 xmlns="{OSPF}"><sites><site><neighbors><neighbor><area-id>0.0.0.0</area-id><if-name>100GE1/0/1</if-name>
<router-id>10.10.0.1</router-id><ip-addr>10.11.0.1</ip-addr><state>Full</state></neighbor></neighbors></site></sites></ospfv2>
</instance></instances></network-instance></data>"""
SYNTHETIC_TUNNELS = f"""<data><nvo3 xmlns="{NVO3}"><tunnels><tunnel><id>1</id><source>10.13.0.1</source>
<destination>10.13.0.2</destination><state>up</state></tunnel></tunnels></nvo3></data>"""


def test_established_peer_ospf_neighbour_and_tunnel_become_metrics():
    plane = control.parse({"instances": SYNTHETIC_INSTANCES, "tunnels": SYNTHETIC_TUNNELS, "macs": None})
    assert plane.bgp[0].seconds == 2 * 86400 + 3 * 3600
    metrics = FabricCollector()
    metrics.record_control("leaf1", plane, 0.2)
    text = exposition(metrics)
    peer = dict(device="leaf1", vpn="_public_", peer="10.10.0.1", family="evpn")
    assert value(text, "fabric_bgp_peer_up", **peer) == 1
    assert value(text, "fabric_bgp_peer_prefixes", **peer, kind="active") == 38
    assert value(text, "fabric_ospf_neighbor_up", device="leaf1", vpn="_public_", interface="100GE1/0/1",
                 neighbor="10.10.0.1", address="10.11.0.1") == 1
    assert value(text, "fabric_vxlan_tunnel_up", device="leaf1", source="10.13.0.1", destination="10.13.0.2") == 1
    assert value(text, "fabric_control_query_refused", device="leaf1", query="macs") == 1
    assert value(text, "fabric_collector_poll_duration_seconds", device="leaf1", poll="control") == 0.2


def test_the_lab_states_down_are_down():
    metrics = FabricCollector()
    metrics.record_control("leaf1", control.parse({**EXAMPLE, "instances": recorded("bgp-peer-states")}))
    text = exposition(metrics)
    assert value(text, "fabric_bgp_peer_up", device="leaf1", vpn="_public_", peer="10.10.0.1", family="ipv4uni") == 0
    assert value(text, "fabric_mlag_heartbeat_up", device="leaf1") == 0
    assert value(text, "fabric_mlag_peer_link_up", device="leaf1", interface="Eth-Trunk0") == 0


def refused() -> RPCError:
    error = RPCError.__new__(RPCError)
    Exception.__init__(error, "Unexpected element")
    return error


class FakeConn:
    connected = True

    def __init__(self, refuse=()):
        self.refuse = set(refuse)

    def get(self, filter):
        name = next(n for n, q in control.QUERIES.items() if q == filter[1])
        if name in self.refuse:
            raise refused()
        if name == "protection":
            return type("Reply", (), {"data_xml": (FIXTURES / "m52-protection-state.xml").read_text()})()
        return type("Reply", (), {"data_xml": recorded({"instances": "bgp-peer-states", "mlag": "mlag-states",
                                                        "tunnels": "nvo3-tunnels", "macs": "mac-statistics"}[name])})()


def test_a_refused_query_leaves_out_that_part_only():
    session = main.Session(target=None, login="x", password="y")
    session.conn = FakeConn(refuse={"mlag"})
    plane = session.get_control()
    assert plane.missing == {"mlag"} and plane.dfs == {} and plane.bgp


class ControlSession(FakeSession):
    def __init__(self, replies=(), planes=()):
        super().__init__(replies)
        self.planes = list(planes)

    def get_control(self):
        plane = self.planes.pop(0) if self.planes else control.ControlState()
        if isinstance(plane, Exception):
            raise plane
        return plane


def test_control_polls_follow_state_polls_and_survive_the_other_polls():
    """the control poll runs in a state poll's slot (counters slots have no room)."""
    collector = main.Collector(FabricCollector(), "x", "y", counter_interval=5, state_interval=25, control_interval=30)
    assert collector.control_every == collector.state_every == 5             # 30 s rounds to the state interval
    metrics = FabricCollector()
    session = ControlSession(planes=[control.parse(EXAMPLE)])
    asyncio.run(main.poll_device("leaf1", session, metrics, 0, 5, 0, attempts=7, control_every=5))
    snap = metrics.snapshots["leaf1"]
    assert snap.polls["control"] == 2 and snap.control is not None          # ticks 0 and 5, both state ticks
    assert snap.polls["state"] == 2 and snap.polls["counters"] == 5


def test_a_control_poll_transport_failure_fails_the_poll():
    metrics = FabricCollector()
    session = ControlSession(planes=[ConnectionError("session closed")])
    asyncio.run(main.poll_device("leaf1", session, metrics, 0, 5, 0, attempts=1, control_every=5))
    assert metrics.snapshots["leaf1"].ok is False and session.dropped == 1


# protection state. Nothing was tripped in the example (no loop test), so a
# trip is synthetic, from the R025 YANG's names and enumerations.
IFM, STORM, LOOP = control.protection.IFM, control.protection.STORM, control.protection.LOOP
SYNTHETIC_TRIPS = f"""<data><ifm xmlns="{IFM}"><interfaces>
<interface><name>10GE1/0/10</name><loopdetect xmlns="{LOOP}"><loopback-detect-infos><status>error-down</status>
<recovery-time>45</recovery-time><action>shutdown</action></loopback-detect-infos></loopdetect>
<error-down><cause>loopback-detect</cause><recovery-time>45</recovery-time><remainder-time>31</remainder-time></error-down></interface>
<interface><name>10GE1/0/1</name><storm-control xmlns="{STORM}"><storm-control-state><packet-type>broadcast</packet-type>
<punish>block</punish></storm-control-state></storm-control></interface>
<interface><name>10GE1/0/11</name><error-down><cause>bpdu-protection</cause><remainder-time>12</remainder-time></error-down></interface>
<interface><name>10GE1/0/12</name><storm-control xmlns="{STORM}"><storm-control-state><punish>normal</punish></storm-control-state></storm-control>
<error-down><cause>link-flap</cause></error-down></interface>
</interfaces></ifm></data>"""


def test_leaf1_has_nothing_tripped():
    xml = (FIXTURES / "m52-protection-state.xml").read_text()
    assert control.protection.parse(xml) == {}
    assert control.parse({"protection": xml}).protection == {}


def test_tripped_ports_become_metrics():
    plane = control.parse({"protection": SYNTHETIC_TRIPS})
    assert set(plane.protection) == {"10GE1/0/10", "10GE1/0/1", "10GE1/0/11"}   # normal storm, other causes: nothing
    metrics = FabricCollector()
    metrics.record_control("leaf1", plane, 0.3)
    text = exposition(metrics)
    assert value(text, "fabric_port_protection_tripped", device="leaf1", interface="10GE1/0/10",
                 cause="loopback-detect", state="error-down", detail="") == 1
    assert value(text, "fabric_port_protection_reopen_seconds", device="leaf1", interface="10GE1/0/10", cause="loopback-detect") == 31
    assert value(text, "fabric_port_protection_tripped", device="leaf1", interface="10GE1/0/1",
                 cause="storm-control", state="block", detail="broadcast") == 1
    assert value(text, "fabric_port_protection_reopen_seconds", device="leaf1", interface="10GE1/0/1", cause="storm-control") is None
    assert value(text, "fabric_port_protection_reopen_seconds", device="leaf1", interface="10GE1/0/11", cause="bpdu-protection") == 12
