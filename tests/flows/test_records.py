"""The flows service: a GoFlow2 record becomes a stored row; its context from the example model."""

import asyncio
import ipaddress

from flows import __main__ as service
from flows.records import Context, row
from orchestrator.prometheus import PrometheusClient
from tests.collector.test_collector import example_netbox

CTX = Context(exporters={"172.24.0.11": ("leaf1", "example")},
              ifnames={("leaf1", 13): "10GE1/0/3", ("leaf1", 5): "100GE1/0/1"},
              edge={("leaf1", "10GE1/0/3")},
              subnets={"example": [(ipaddress.ip_network("10.100.0.0/24"), "web"), (ipaddress.ip_network("10.100.1.0/24"), "db")]})

# The shape GoFlow2 v2 documents for `-format json` (not yet seen from leaf1).
RECORD = {"type": "NETFLOW_V9", "time_received_ns": 1_760_620_000_000_000_000, "sampling_rate": 0,
          "sampler_address": "172.24.0.11", "bytes": 1500, "packets": 2, "src_addr": "10.100.0.10",
          "dst_addr": "10.100.1.20", "etype": "IPv4", "proto": "TCP", "src_port": 51000, "dst_port": 443, "in_if": 13}


def test_a_record_from_an_edge_port_is_named_scaled_and_placed_in_its_segments():
    r = row(RECORD, CTX)
    assert (r["exporter"], r["interface"], r["kind"], r["proto"], r["dst_port"]) == ("leaf1", "10GE1/0/3", "edge", 6, 443)
    assert (r["bytes"], r["packets"]) == (1500 * 1024, 2 * 1024)        # no rate in the record: the platform's 1:1024
    assert (r["src_segment"], r["dst_segment"]) == ("web", "db")
    assert r["time"] == "2025-10-16 13:06:40"


def test_uplink_records_unknown_switches_and_ipv6_are_told_apart():
    assert row({**RECORD, "in_if": 5, "sampling_rate": 512}, CTX)["kind"] == "vxlan"
    assert row({**RECORD, "in_if": 5, "sampling_rate": 512}, CTX)["bytes"] == 1500 * 512
    assert row({**RECORD, "in_if": 99}, CTX)["interface"] == "ifIndex 99"
    assert row({**RECORD, "sampler_address": "192.0.2.9"}, CTX) is None
    assert row({**RECORD, "src_addr": "2001:db8::1"}, CTX) is None
    assert row({**RECORD, "proto": 17}, CTX)["proto"] == 17
    assert row({**RECORD, "dst_addr": "8.8.8.8"}, CTX)["dst_segment"] == ""


class Prom(PrometheusClient):
    def __init__(self):
        super().__init__("http://prometheus")

    async def query(self, expr):
        assert expr == "fabric_interface_ifindex"
        return [({"device": "leaf1", "interface": "10GE1/0/3"}, 0.0, 13.0)]


def test_context_from_the_lab_model():
    from orchestrator.lifecycle import Lifecycle
    from orchestrator.segments import Segments

    nb = example_netbox()
    asyncio.run(Lifecycle(nb).activate("leaf1", "172.24.0.11/24"))
    asyncio.run(Segments(nb).create("example", "web", 10500, 500))
    asyncio.run(Segments(nb).attach("example", "leaf1", "Eth-Trunk1", 10500, 500))
    ctx = asyncio.run(service.load_context(nb, Prom()))
    assert ctx.exporters["172.24.0.11"] == ("leaf1", "example")
    assert {("leaf1", "Eth-Trunk1"), ("leaf1", "10GE1/0/1"), ("leaf1", "10GE1/0/2")} <= ctx.edge   # trunk and members
    assert ctx.ifnames == {("leaf1", 13): "10GE1/0/3"}
