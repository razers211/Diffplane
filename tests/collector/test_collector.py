import asyncio
import re
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest

import collector.__main__ as main
from orchestrator import hostkeys
from orchestrator.hostkeys import md5_fingerprint
from collector.metrics import FabricCollector
from collector.state import Neighbor, parse
from tests.seed.fake_netbox import FakeNetBox
from tests.helpers import seed

FIXTURE = (Path(__file__).parent / "fixtures" / "leaf1-ifm.xml").read_text()


def test_parse_interface_state():
    state = parse(FIXTURE)

    assert state.oper_up == {
        "100GE1/0/1": False,
        "100GE1/0/2": True,
        "MEth0/0/0": True,
        "Eth-Trunk1.500": False,
    }


def test_parse_lldp_neighbors():
    assert parse(FIXTURE).neighbors == [
        Neighbor(
            interface="100GE1/0/2",
            system_name="spine1",
            port_id="100GE1/0/9",
            chassis_id="0200-0000-00f1",
            port_description="to leaf1",
        )
    ]


def test_parse_counters():
    state = parse(FIXTURE)

    assert state.counters["MEth0/0/0"] == {"in_octets": 46349930, "out_octets": 54195782, "in_errors": 0,
                                           "out_errors": 0, "in_discards": 0, "out_discards": 0}
    assert state.counters["100GE1/0/2"]["in_errors"] == 3 and state.counters["100GE1/0/2"]["out_discards"] == 7
    assert "100GE1/0/1" not in state.counters   # no statistics in the reply: no counters


def test_counters_are_exported_as_prometheus_counters():
    metrics = FabricCollector()
    metrics.record_success("leaf1", parse(FIXTURE))

    text = exposition(metrics)

    # The TYPE line names the family with or without _total, depending on prometheus_client.
    assert re.search(r"^# TYPE fabric_interface_in_octets(_total)? counter$", text, re.M)
    assert 'fabric_interface_in_octets_total{device="leaf1",interface="MEth0/0/0"} 4.634993e+07' in text
    assert 'fabric_interface_out_discards_total{device="leaf1",interface="100GE1/0/2"} 7.0' in text


def test_bandwidth_of_ports_and_trunks_is_exported():
    """the speed each port runs at, from the state poll; sub-interfaces left out."""
    metrics = FabricCollector()
    state = parse(FIXTURE)
    assert state.bandwidth == {"100GE1/0/1": 100_000_000_000, "MEth0/0/0": 100_000_000}
    metrics.record_success("leaf1", state, "state")
    metrics.record_success("leaf1", parse(FIXTURE.replace("<bandwidth>", "<x>").replace("</bandwidth>", "</x>")), "counters")
    text = exposition(metrics)
    assert 'fabric_interface_bandwidth_bps{device="leaf1",interface="MEth0/0/0"} 1e+08' in text   # kept between state polls
    assert "Eth-Trunk1.500" not in text.split("fabric_interface_bandwidth_bps")[-1].split("# HELP")[0]
    assert state.ifindex == {"100GE1/0/1": 5, "MEth0/0/0": 4}   # as the example's own (yang-ifm probe)
    assert 'fabric_interface_ifindex{device="leaf1",interface="100GE1/0/1"} 5.0' in text


def test_parse_empty_reply():
    state = parse('<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>')

    assert state.oper_up == {} and state.neighbors == []


def exposition(metrics: FabricCollector) -> str:
    registry = CollectorRegistry()
    registry.register(metrics)
    return generate_latest(registry).decode()


def test_metrics_for_successful_poll():
    metrics = FabricCollector()
    metrics.record_success("leaf1", parse(FIXTURE))

    text = exposition(metrics)

    assert 'fabric_collector_device_up{device="leaf1"} 1.0' in text
    assert 'fabric_interface_oper_up{device="leaf1",interface="100GE1/0/2"} 1.0' in text
    assert 'fabric_interface_oper_up{device="leaf1",interface="100GE1/0/1"} 0.0' in text
    assert (
        'fabric_lldp_neighbor_info{device="leaf1",interface="100GE1/0/2",'
        'neighbor_chassis_id="0200-0000-00f1",neighbor_port_id="100GE1/0/9",'
        'neighbor_system_name="spine1"} 1.0'
    ) in text
    assert 'fabric_collector_last_success_timestamp_seconds{device="leaf1"}' in text


def test_failure_drops_state_but_keeps_last_success():
    metrics = FabricCollector()
    metrics.record_success("leaf1", parse(FIXTURE))
    metrics.record_failure("leaf1", "timeout")

    text = exposition(metrics)

    assert 'fabric_collector_device_up{device="leaf1"} 0.0' in text
    assert 'fabric_collector_last_success_timestamp_seconds{device="leaf1"}' in text
    assert "fabric_interface_oper_up{" not in text
    assert "fabric_lldp_neighbor_info{" not in text


def test_devices_no_longer_active_are_forgotten():
    metrics = FabricCollector()
    metrics.record_success("leaf1", parse(FIXTURE))
    metrics.record_success("old", parse(FIXTURE))

    metrics.forget_others({"leaf1"})

    assert set(metrics.snapshots) == {"leaf1"}


def example_netbox() -> FakeNetBox:
    from seed.model import load

    nb = FakeNetBox()
    asyncio.run(seed(nb, load(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml")))
    return nb


# A NETCONF host key pinned on leaf1 (orchestrator/hostkeys.py); any valid-looking key will do.
PINNED = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBExample"


def pinned_lab_netbox() -> FakeNetBox:
    nb = example_netbox()
    leaf = asyncio.run(nb.find_one("dcim/devices", name="leaf1"))
    asyncio.run(nb.update("dcim/devices", leaf["id"], {"custom_fields": {"ssh_host_keys": {"22": [PINNED],
                                                                                          "830": [PINNED]}}}))
    return nb


class FakeSession:
    """Answers polls from a script: a DeviceState or an exception per call."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.filters, self.dropped, self.opened = [], 0, 0

    def get(self, filter_xml):
        self.filters.append(filter_xml)
        if self.opened == 0 or self.dropped >= self.opened:
            self.opened += 1
        reply = self.replies.pop(0) if self.replies else parse(FIXTURE)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def drop(self):
        self.dropped += 1


def run_polls(session, attempts, metrics=None, state_every=5):
    metrics = metrics or FabricCollector()
    asyncio.run(main.poll_device("leaf1", session, metrics, 0, state_every, 0, attempts))
    return metrics


def test_active_devices_with_a_pin_become_targets():
    devices = asyncio.run(main.active_devices(pinned_lab_netbox()))

    assert set(devices) == {"leaf1"}   # planned devices have no management address
    target = devices["leaf1"]
    assert target.address == "172.24.0.11" and target.pins == (PINNED,)
    assert target.check("172.24.0.11", md5_fingerprint(PINNED)) is True
    assert asyncio.run(main.active_devices(example_netbox())) == {"leaf1": None}


def test_state_first_then_counters_in_one_session():
    """a state/LLDP poll every 5th tick, counters of main interfaces in between."""
    from collector.state import COUNTERS_FILTER, STATE_FILTER

    session = FakeSession()
    run_polls(session, 6)

    assert session.filters == [STATE_FILTER] + [COUNTERS_FILTER] * 4 + [STATE_FILTER]
    assert session.opened == 1 and session.dropped == 0


def test_a_failed_poll_drops_the_session_and_starts_over_with_state():
    from collector.state import STATE_FILTER

    session = FakeSession([parse(FIXTURE), TimeoutError("no answer"), parse(FIXTURE)])
    metrics = run_polls(session, 3)

    assert session.dropped == 1
    assert session.filters[2] == STATE_FILTER   # after the failure: state again
    assert metrics.snapshots["leaf1"].ok and metrics.snapshots["leaf1"].failures == 1
    text = exposition(metrics)
    assert 'fabric_collector_poll_failures_total{device="leaf1"} 1.0' in text
    assert 'fabric_collector_poll_duration_seconds{device="leaf1",poll="state"}' in text
    # Successful polls are counted, by kind: the failure is not one of them.
    assert 'fabric_collector_polls_total{device="leaf1",poll="state"} 2.0' in text


def test_only_physical_ports_keep_counters():
    from collector.state import DeviceState

    metrics = FabricCollector()
    state = parse(FIXTURE)
    metrics.record_success("leaf1", state, "state")
    counters = DeviceState(oper_up={"Eth-Trunk1": True, "10GE1/0/3": True},
                           counters={"Eth-Trunk1": {"in_octets": 9}, "10GE1/0/3": {"in_octets": 5},
                                     "Eth-Trunk1.500": {"in_octets": 1}})
    metrics.record_success("leaf1", counters, "counters")

    merged = metrics.snapshots["leaf1"].state
    assert merged.counters == {"10GE1/0/3": {"in_octets": 5}}
    assert merged.neighbors == state.neighbors   # LLDP kept from the state poll
    assert merged.oper_up["Eth-Trunk1"] is True and merged.oper_up["MEth0/0/0"] is True


def test_collector_follows_netbox_and_restarts_on_a_new_pin():
    sessions = []

    def factory(target, login, password):
        sessions.append(target)
        return FakeSession()

    async def scenario():
        metrics = FabricCollector()
        collector = main.Collector(metrics, "u", "p", 3600, 3600, session_factory=factory)
        first = main.Target("172.24.0.11", ("ssh-rsa AAAA",))
        await collector.refresh({"leaf1": first, "leaf2": None})
        await asyncio.sleep(0)
        await collector.refresh({"leaf1": main.Target("172.24.0.11", ("ssh-rsa AAAA",))})   # same pin
        await collector.refresh({"leaf1": main.Target("172.24.0.11", ("ssh-rsa BBBB",))})   # re-pinned
        running = set(collector.tasks)
        await collector.stop()
        return metrics, running, collector.tasks

    metrics, running, left = asyncio.run(scenario())

    assert [t.pins for t in sessions] == [("ssh-rsa AAAA",), ("ssh-rsa BBBB",)]
    assert running == {"leaf1"} and left == {}
    assert "leaf2" not in metrics.snapshots   # forgotten once NetBox no longer lists it


def test_unpinned_devices_are_not_polled():
    nb = example_netbox()   # its seeding runs its own event loop: build it first

    async def scenario():
        metrics = FabricCollector()
        collector = main.Collector(metrics, "u", "p", 5, 25,
                                   session_factory=lambda *_: (_ for _ in ()).throw(AssertionError("polled")))
        await collector.refresh(await main.active_devices(nb))
        return metrics

    metrics = asyncio.run(scenario())
    assert metrics.snapshots["leaf1"].ok is False
    assert "no pinned NETCONF host key" in metrics.snapshots["leaf1"].error


def test_connect_verifies_the_pinned_key_before_login(monkeypatch):
    seen = {}
    monkeypatch.setattr(main.manager, "connect", lambda **kwargs: seen.update(kwargs) or "session")
    target = asyncio.run(main.active_devices(pinned_lab_netbox()))["leaf1"]

    assert main.connect(target, "u", "p") == "session"
    # No hostkey_b64: it would negotiate only "ssh-rsa", which V300 refuses (rsa-sha2-* only).
    assert seen["hostkey_verify"] is True and "hostkey_b64" not in seen and seen["port"] == 830
    assert seen["unknown_host_cb"]("172.24.0.11", md5_fingerprint(PINNED)) is True
    assert seen["unknown_host_cb"]("172.24.0.11", "aa:bb") is False


def test_filters_split_counters_from_state_and_lldp():
    from collector.state import COUNTERS_FILTER, FILTER, STATE_FILTER

    assert "<mib-statistics>" in COUNTERS_FILTER and "lldp" not in COUNTERS_FILTER and "bandwidth" not in COUNTERS_FILTER
    assert "<bandwidth/>" in STATE_FILTER and "<index/>" in STATE_FILTER and "<index/>" not in COUNTERS_FILTER
    assert "<class>main-interface</class>" in COUNTERS_FILTER
    assert "<neighbors/>" in STATE_FILTER and "mib-statistics" not in STATE_FILTER and "<class/>" in STATE_FILTER
    assert "<mib-statistics>" in FILTER and "<neighbors/>" in FILTER


def test_counters_filter_can_leave_out_sub_interfaces():
    from collector.state import subtree

    assert "<class>main-interface</class>" in subtree(lldp=False, main_only=True)
    assert "<class/>" in subtree(lldp=False)


def test_measure_reads_board_core_and_memory_usage():
    """Shape of leaf1's huawei-cpu-memory reply."""
    from collector.measure import cpu_sample

    ns = "urn:huawei:yang:huawei-cpu-memory"
    cores = "".join(f"<board-cpu-core-info><slot-id>1</slot-id><cpu-id>0</cpu-id><core-id>{n}</core-id>"
                    f"<cpu-core-usage>{u}</cpu-core-usage></board-cpu-core-info>" for n, u in ((0, 43), (1, 0)))
    xml = (
        f'<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><cpu-memory xmlns="{ns}">'
        "<board-cpu-infos><board-cpu-info><slot-id>1</slot-id><system-cpu-usage>32</system-cpu-usage>"
        "</board-cpu-info></board-cpu-infos>"
        f"<board-cpu-core-infos>{cores}</board-cpu-core-infos>"
        "<board-memory-infos><board-memory-info><slot-id>1</slot-id><os-memory-usage>44</os-memory-usage>"
        "</board-memory-info></board-memory-infos></cpu-memory></data>"
    )
    assert cpu_sample(xml) == {"system (slot 1)": 32.0, "core 0": 43.0, "core 1": 0.0, "memory (slot 1)": 44.0}


def test_the_collector_takes_the_login_set_in_the_gui(tmp_path):
    """the orchestrator writes the GUI's collector login into the shared volume."""
    from collector.__main__ import shared_login

    assert shared_login(str(tmp_path)) is None                    # none set: the .env login is used
    (tmp_path / "collector.json").write_text('{"login": "reader", "password": "s3cret-pw"}')
    assert shared_login(str(tmp_path)) == ("reader", "s3cret-pw")
    (tmp_path / "collector.json").write_text('{"login": null, "password": null}')
    assert shared_login(str(tmp_path)) is None
