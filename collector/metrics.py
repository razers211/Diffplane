"""Prometheus exposition of the latest collected state of every device."""

import time
from dataclasses import dataclass, field

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from collector.control import BGP_UP, HEARTBEAT_OK, OSPF_UP, ControlState
from collector.hardware import HardwareState
from collector.state import COUNTERS, DeviceState, is_physical

FAST, FULL = "counters", "state"   # the two polls: counters every 5 s, state and LLDP every 25 s
CONTROL = "control"                # control-plane state every 25 s
# hardware groups (collector.hardware.GROUPS), each its own poll kind: environment, optics


@dataclass
class Snapshot:
    ok: bool
    state: DeviceState | None
    last_success: float | None
    error: str = ""
    durations: dict[str, float] = field(default_factory=dict)   # poll kind -> seconds, last poll
    failures: int = 0     # failed polls since the collector started
    reconnects: int = 0   # NETCONF sessions opened again after the first
    polls: dict[str, int] = field(default_factory=dict)   # poll kind -> successful polls since start
    control: ControlState | None = None   # the last control-plane poll
    hardware: dict[str, HardwareState] = field(default_factory=dict)   # group -> its last poll


class FabricCollector(Collector):
    """Renders metrics from snapshots at scrape time, so stale series never linger."""

    def __init__(self) -> None:
        self.snapshots: dict[str, Snapshot] = {}

    def record_success(self, device: str, state: DeviceState, kind: str = FULL, duration: float | None = None) -> None:
        """Merge one poll into the device's state. A state poll brings every interface's oper
        status and the LLDP neighbours; a counters poll brings the counters of physical ports
        (and their oper status)."""
        previous = self.snapshots.get(device)
        merged = DeviceState()
        if previous is not None and previous.state is not None:
            merged = DeviceState(dict(previous.state.oper_up), list(previous.state.neighbors),
                                 dict(previous.state.counters), dict(previous.state.bandwidth),
                                 dict(previous.state.ifindex))
        if kind == FULL:
            merged.oper_up, merged.neighbors = dict(state.oper_up), list(state.neighbors)
            merged.bandwidth, merged.ifindex = dict(state.bandwidth), dict(state.ifindex)
        else:
            merged.oper_up.update(state.oper_up)
        physical = {name: values for name, values in state.counters.items() if is_physical(name)}
        if physical or kind == FAST:
            merged.counters = physical
        snapshot = Snapshot(ok=True, state=merged, last_success=time.time(),
                            durations=dict(previous.durations) if previous else {},
                            failures=previous.failures if previous else 0,
                            reconnects=previous.reconnects if previous else 0,
                            polls=dict(previous.polls) if previous else {},
                            control=previous.control if previous else None,
                            hardware=dict(previous.hardware) if previous else {})
        snapshot.polls[kind] = snapshot.polls.get(kind, 0) + 1
        if duration is not None:
            snapshot.durations[kind] = duration
        self.snapshots[device] = snapshot

    def _carried(self, device: str, kind: str, duration: float | None) -> Snapshot:
        """The device's snapshot for a poll whose state the other polls carry over, counted."""
        snapshot = self.snapshots.get(device)
        if snapshot is None or snapshot.state is None:
            snapshot = Snapshot(ok=True, state=DeviceState(), last_success=time.time(),
                                durations=dict(snapshot.durations) if snapshot else {},
                                failures=snapshot.failures if snapshot else 0,
                                reconnects=snapshot.reconnects if snapshot else 0,
                                polls=dict(snapshot.polls) if snapshot else {})
            self.snapshots[device] = snapshot
        snapshot.polls[kind] = snapshot.polls.get(kind, 0) + 1
        if duration is not None:
            snapshot.durations[kind] = duration
        return snapshot

    def record_control(self, device: str, plane: ControlState, duration: float | None = None) -> None:
        """Keep the device's latest control-plane state; the other polls carry it over."""
        self._carried(device, CONTROL, duration).control = plane

    def record_hardware(self, device: str, group: str, parts: HardwareState, duration: float | None = None) -> None:
        """Keep the device's latest read of one hardware group, carried over like control."""
        self._carried(device, group, duration).hardware[group] = parts

    def record_failure(self, device: str, error: str) -> None:
        previous = self.snapshots.get(device)
        self.snapshots[device] = Snapshot(
            ok=False,
            state=None,
            last_success=previous.last_success if previous else None,
            error=error,
            durations=previous.durations if previous else {},
            failures=(previous.failures if previous else 0) + 1,
            reconnects=previous.reconnects if previous else 0,
            polls=dict(previous.polls) if previous else {},
        )

    def record_reconnect(self, device: str) -> None:
        if device in self.snapshots:
            self.snapshots[device].reconnects += 1

    def forget_others(self, devices: set[str]) -> None:
        for name in set(self.snapshots) - devices:
            del self.snapshots[name]

    def collect(self):
        up = GaugeMetricFamily("fabric_collector_device_up", "1 if the last poll succeeded", labels=["device"])
        last = GaugeMetricFamily(
            "fabric_collector_last_success_timestamp_seconds", "Time of the last successful poll", labels=["device"]
        )
        duration = GaugeMetricFamily("fabric_collector_poll_duration_seconds",
                                     "How long the last poll of this kind took", labels=["device", "poll"])
        failures = CounterMetricFamily("fabric_collector_poll_failures", "Failed polls since the collector started",
                                       labels=["device"])
        # Counted here, not inferred from timestamps: polls and scrapes both run every 5 s, so
        # changes() of the last-success time undercounts.
        polls = CounterMetricFamily("fabric_collector_polls", "Successful polls since the collector started",
                                    labels=["device", "poll"])
        reconnects = CounterMetricFamily("fabric_collector_reconnects",
                                         "NETCONF sessions reopened since the collector started", labels=["device"])
        oper = GaugeMetricFamily(
            "fabric_interface_oper_up", "1 if the interface is operationally up", labels=["device", "interface"]
        )
        bandwidth = GaugeMetricFamily(
            "fabric_interface_bandwidth_bps", "Bits per second the switch reports for a port or Eth-Trunk "
            "(a trunk: its selected members)", labels=["device", "interface"])
        ifindex = GaugeMetricFamily("fabric_interface_ifindex", "The switch's ifIndex of a port or Eth-Trunk "
                                    "(NetStream records name their ports by it)", labels=["device", "interface"])
        lldp = GaugeMetricFamily(
            "fabric_lldp_neighbor_info",
            "An LLDP neighbour seen on an interface",
            labels=["device", "interface", "neighbor_system_name", "neighbor_port_id", "neighbor_chassis_id"],
        )
        # fabric_interface_in_octets_total, ..._out_discards_total: the switch's own cumulative
        # counters (huawei-ifm mib-statistics) of physical ports; Prometheus' rate() turns them into traffic.
        counters = {
            name: CounterMetricFamily(f"fabric_interface_{name}", f"Interface {name.replace('_', ' ')} "
                                      "(cumulative, from the switch)", labels=["device", "interface"])
            for name in COUNTERS
        }
        plane, hw = _control_families(), _hardware_families()
        for device, snap in sorted(self.snapshots.items()):
            if snap.control is not None:
                _add_control(plane, device, snap.control)
            for parts in snap.hardware.values():
                _add_hardware(hw, device, parts)
            up.add_metric([device], 1 if snap.ok else 0)
            failures.add_metric([device], snap.failures)
            reconnects.add_metric([device], snap.reconnects)
            for kind, count in sorted(snap.polls.items()):
                polls.add_metric([device, kind], count)
            for kind, seconds in sorted(snap.durations.items()):
                duration.add_metric([device, kind], seconds)
            if snap.last_success is not None:
                last.add_metric([device], snap.last_success)
            if snap.state is None:
                continue
            for interface, is_up in sorted(snap.state.oper_up.items()):
                oper.add_metric([device, interface], 1 if is_up else 0)
            for interface, bps in sorted(snap.state.bandwidth.items()):
                bandwidth.add_metric([device, interface], bps)
            for interface, index in sorted(snap.state.ifindex.items()):
                ifindex.add_metric([device, interface], index)
            for n in snap.state.neighbors:
                lldp.add_metric([device, n.interface, n.system_name, n.port_id, n.chassis_id], 1)
            for interface, values in sorted(snap.state.counters.items()):
                for name, value in values.items():
                    counters[name].add_metric([device, interface], value)
        yield from (up, last, duration, failures, polls, reconnects, oper, bandwidth, ifindex, lldp, *counters.values(), *plane.values(), *hw.values())


def _control_families() -> dict:
    """Control-plane metric families (control-plane state of the last 30 s poll). The VPN instance is the label
    `vpn`: Prometheus keeps `instance` for the scrape target (it renamed ours exported_instance)."""
    g = GaugeMetricFamily
    return {
        "bgp_up": g("fabric_bgp_peer_up", "1 if the BGP peer is established, per address family",
                    labels=["device", "vpn", "peer", "family"]),
        "bgp_info": g("fabric_bgp_peer_info", "A BGP peer and its state (Idle, Active, Established, ...)",
                      labels=["device", "vpn", "peer", "family", "state", "remote_as"]),
        "bgp_seconds": g("fabric_bgp_peer_state_seconds", "How long the peer has been in its current state",
                         labels=["device", "vpn", "peer", "family"]),
        "bgp_prefixes": g("fabric_bgp_peer_prefixes", "Prefixes received, active (received and best) and advertised",
                          labels=["device", "vpn", "peer", "family", "kind"]),
        "ospf_up": g("fabric_ospf_neighbor_up", "1 if the OSPF neighbour is Full",
                     labels=["device", "vpn", "interface", "neighbor", "address"]),
        "ospf_info": g("fabric_ospf_neighbor_info", "An OSPF neighbour and its state",
                       labels=["device", "vpn", "area", "interface", "neighbor", "state"]),
        "routes": g("fabric_routes", "Routes per VPN instance and protocol (total, active)",
                    labels=["device", "vpn", "protocol", "kind"]),
        "dfs_info": g("fabric_mlag_dfs_info", "The DFS group: role, state, why", labels=["device", "role", "state", "causation"]),
        "heartbeat": g("fabric_mlag_heartbeat_up", "1 if the M-LAG heartbeat with the peer is ok", labels=["device"]),
        "peer_link": g("fabric_mlag_peer_link_up", "1 if the M-LAG peer-link is up", labels=["device", "interface"]),
        "mlag_port": g("fabric_mlag_port_up", "1 if the M-LAG trunk is up", labels=["device", "interface"]),
        "tunnel": g("fabric_vxlan_tunnel_up", "1 if the VXLAN tunnel to a VTEP is up", labels=["device", "source", "destination"]),
        "macs": g("fabric_mac_entries", "MAC addresses learnt, by kind (and the table's capacity)", labels=["device", "kind"]),
        "tripped": g("fabric_port_protection_tripped", "1 while storm control, loop detection or BPDU protection "
                     "acts on the port: cause, state, packet type", labels=["device", "interface", "cause", "state", "detail"]),
        "reopen": g("fabric_port_protection_reopen_seconds", "Seconds before an error-down port reopens by itself",
                    labels=["device", "interface", "cause"]),
        "missing": g("fabric_control_query_refused", "1 for a control-plane query this switch refuses", labels=["device", "query"]),
    }


def _add_control(f: dict, device: str, c: ControlState) -> None:
    for p in c.bgp:
        key = [device, p.instance, p.address, p.family]
        f["bgp_up"].add_metric(key, 1 if p.state == BGP_UP else 0)
        f["bgp_info"].add_metric([*key, p.state, p.remote_as], 1)
        if p.seconds is not None:
            f["bgp_seconds"].add_metric(key, p.seconds)
        for kind, value in (("received", p.received), ("active", p.active), ("advertised", p.advertised)):
            if value is not None:
                f["bgp_prefixes"].add_metric([*key, kind], value)
    for n in c.ospf:
        f["ospf_up"].add_metric([device, n.instance, n.interface, n.router_id, n.address], 1 if n.state == OSPF_UP else 0)
        f["ospf_info"].add_metric([device, n.instance, n.area, n.interface, n.router_id, n.state], 1)
    for (instance, protocol), (total, active) in sorted(c.routes.items()):
        f["routes"].add_metric([device, instance, protocol, "total"], total)
        f["routes"].add_metric([device, instance, protocol, "active"], active)
    if c.dfs:
        f["dfs_info"].add_metric([device, c.dfs["role"], c.dfs["state"], c.dfs["causation"]], 1)
        f["heartbeat"].add_metric([device], 1 if c.dfs["heartbeat"] == HEARTBEAT_OK else 0)
    for interface, is_up in sorted(c.peer_links.items()):
        f["peer_link"].add_metric([device, interface], 1 if is_up else 0)
    for interface, is_up in sorted(c.mlag_ports.items()):
        f["mlag_port"].add_metric([device, interface], 1 if is_up else 0)
    for t in c.tunnels:
        f["tunnel"].add_metric([device, t.source, t.destination], 1 if t.state == "up" else 0)
    for kind, value in sorted(c.macs.items()):
        f["macs"].add_metric([device, kind], value)
    for interface, trips in sorted(c.protection.items()):
        for t in trips:
            f["tripped"].add_metric([device, interface, t.cause, t.state, t.detail], 1)
            if t.reopen is not None:
                f["reopen"].add_metric([device, interface, t.cause], t.reopen)
    for query in sorted(c.missing):
        f["missing"].add_metric([device, query], 1)


def _hardware_families() -> dict:
    """Hardware metric families. Thresholds are the switch's or the module's own, as series beside the
    readings, so alert rules compare the two."""
    g = GaugeMetricFamily
    port = ["device", "interface"]
    return {
        "optic_info": g("fabric_optic_info", "An optical module in a port", labels=[*port, "vendor", "type", "part", "serial"]),
        "optic_temperature": g("fabric_optic_temperature_celsius", "Optical module temperature", labels=port),
        "optic_voltage": g("fabric_optic_voltage_volts", "Optical module supply voltage", labels=port),
        "optic_rx": g("fabric_optic_rx_power_dbm", "Received optical power per channel", labels=[*port, "channel"]),
        "optic_tx": g("fabric_optic_tx_power_dbm", "Transmitted optical power per channel", labels=[*port, "channel"]),
        "optic_bias": g("fabric_optic_bias_milliamperes", "Laser bias current per channel", labels=[*port, "channel"]),
        "optic_threshold": g("fabric_optic_threshold", "The module's own threshold of a measure (dBm, Celsius, V, mA)",
                             labels=[*port, "measure", "level"]),
        "fan_present": g("fabric_fan_present", "1 if the fan is present", labels=["device", "fan"]),
        "fan_speed": g("fabric_fan_speed_percent", "Fan speed set by the switch", labels=["device", "fan"]),
        "fan_rpm": g("fabric_fan_rpm", "Fan speed measured", labels=["device", "fan"]),
        "power_up": g("fabric_power_module_up", "1 if the power module's status is normal", labels=["device", "module"]),
        "power_info": g("fabric_power_module_info", "A power module and its status", labels=["device", "module", "status"]),
        "power_in": g("fabric_power_module_input_watts", "Power module input", labels=["device", "module"]),
        "power_out": g("fabric_power_module_output_watts", "Power module output", labels=["device", "module"]),
        "cpu": g("fabric_cpu_usage_percent", "CPU usage per board", labels=["device", "slot"]),
        "cpu_threshold": g("fabric_cpu_overload_threshold_percent", "The switch's CPU overload threshold", labels=["device", "slot"]),
        "memory": g("fabric_memory_usage_percent", "Memory usage per board", labels=["device", "slot"]),
        "memory_threshold": g("fabric_memory_overload_threshold_percent", "The switch's memory overload threshold",
                              labels=["device", "slot"]),
        "temperature": g("fabric_temperature_celsius", "A temperature sensor", labels=["device", "slot", "sensor"]),
        "temperature_ok": g("fabric_temperature_sensor_normal", "1 if the switch reports the sensor Normal",
                            labels=["device", "slot", "sensor"]),
        "temperature_threshold": g("fabric_temperature_threshold_celsius", "The switch's own sensor threshold",
                                   labels=["device", "slot", "sensor", "level"]),
        "device_info": g("fabric_device_info", "The switch's model, software release and patch, as it reports them",
                         labels=["device", "model", "version", "patch"]),
        "missing": g("fabric_hardware_query_refused", "1 for a hardware query this switch refuses", labels=["device", "query"]),
    }


def _add_hardware(f: dict, device: str, h: HardwareState) -> None:
    for port, o in sorted(h.optics.items()):
        f["optic_info"].add_metric([device, port, o.vendor, o.type, o.part, o.serial], 1)
        for key, value in (("optic_temperature", o.temperature), ("optic_voltage", o.voltage)):
            if value is not None:
                f[key].add_metric([device, port], value)
        for channel, tx, rx, bias in o.channels:
            for key, value in (("optic_tx", tx), ("optic_rx", rx), ("optic_bias", bias)):
                if value is not None:
                    f[key].add_metric([device, port, channel], value)
        for (measure, level), value in sorted(o.thresholds.items()):
            f["optic_threshold"].add_metric([device, port, measure, level], value)
    for fan, (present, speed, rpm) in sorted(h.fans.items()):
        f["fan_present"].add_metric([device, fan], 1 if present else 0)
        for key, value in (("fan_speed", speed), ("fan_rpm", rpm)):
            if value is not None:
                f[key].add_metric([device, fan], value)
    for module, (status, watts_in, watts_out) in sorted(h.power.items()):
        f["power_up"].add_metric([device, module], 1 if status == "normal" else 0)
        f["power_info"].add_metric([device, module, status], 1)
        for key, value in (("power_in", watts_in), ("power_out", watts_out)):
            if value is not None:
                f[key].add_metric([device, module], value)
    for key, readings in (("cpu", h.cpu), ("memory", h.memory)):
        for slot, (usage, threshold) in sorted(readings.items()):
            f[key].add_metric([device, slot], usage)
            if threshold is not None:
                f[f"{key}_threshold"].add_metric([device, slot], threshold)
    for s in h.sensors:
        f["temperature"].add_metric([device, s.slot, s.name], s.celsius)
        if s.status:
            f["temperature_ok"].add_metric([device, s.slot, s.name], 1 if s.status == "Normal" else 0)
        for level, value in sorted(s.thresholds.items()):
            f["temperature_threshold"].add_metric([device, s.slot, s.name, level], value)
    if h.system:
        f["device_info"].add_metric([device, *h.system], 1)
    for query in sorted(h.missing):
        f["missing"].add_metric([device, query], 1)
