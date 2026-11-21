"""Collector: poll active devices over NETCONF and serve Prometheus metrics.

    python -m collector

Read-only by design: it only issues NETCONF <get>. It only talks to a switch
whose NETCONF host key is pinned in NetBox (orchestrator/hostkeys.py); others count as down.

one NETCONF session per switch, kept open (the switch resets rapid
reconnects). Interface counters of physical ports every COLLECTOR_COUNTER_INTERVAL (5 s),
from main interfaces only; the state of every interface and the LLDP neighbours every
COLLECTOR_INTERVAL (25 s), in the same session. Polls of one switch never overlap. After a
failure the session is dropped and the next try waits COLLECTOR_INTERVAL.

control-plane state (BGP and EVPN peers, OSPF neighbours, route counts,
M-LAG/DFS, VXLAN tunnels, MAC counts, collector/control.py) in the same session, right after a state
poll (every 25 s; a counters slot has no room for it: counters take up to ~4.5 s
of 5); COLLECTOR_CONTROL_INTERVAL is rounded to a multiple of COLLECTOR_INTERVAL. A query the switch
refuses leaves out that part only.

hardware (collector/hardware.py) after the control poll, its two groups in
alternate state slots: fans, power, CPU, memory and temperatures, then optics, each every
COLLECTOR_HARDWARE_INTERVAL (50 s; rounded so each group gets every Nth state slot).

Environment:
  NETBOX_URL, NETBOX_TOKEN     device list (read only: active devices with a primary IP)
  COLLECTOR_LOGIN, COLLECTOR_PASSWORD   NETCONF login
  COLLECTOR_COUNTER_INTERVAL   seconds between counter polls (default 5)
  COLLECTOR_INTERVAL           seconds between state/LLDP polls and device list refreshes (default 25)
  COLLECTOR_CONTROL_INTERVAL   seconds between control-plane polls (default 25, a multiple of COLLECTOR_INTERVAL; 0: off)
  COLLECTOR_HARDWARE_INTERVAL  seconds between two reads of each hardware group (default 50; 0: off)
  COLLECTOR_PORT               metrics port (default 9108)
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

from ncclient import manager
from ncclient.operations import RPCError
from prometheus_client import REGISTRY, start_http_server

from collector import control, hardware
from collector.metrics import FAST, FULL, FabricCollector
from collector.state import COUNTERS_FILTER, STATE_FILTER, DeviceState, parse
from orchestrator import hostkeys
from orchestrator.config import Settings
from orchestrator.netbox import NetBoxClient, NetBoxError

log = logging.getLogger("collector")


@dataclass(frozen=True)
class Target:
    address: str
    pins: tuple[str, ...]   # pinned NETCONF keys; a change closes the session (key checked again)
    check: object = field(default=None, compare=False)   # ncclient unknown-host callback from the pins


def shared_login(shared: str | None = None) -> tuple[str, str] | None:
    """The login set in the platform's GUI settings, written by the orchestrator into the
    shared volume; None when there is none (the .env login is used)."""
    path = os.path.join(shared or os.environ.get("PLATFORM_SHARED", "/nonexistent"), "collector.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("login") and data.get("password"):
        return data["login"], data["password"]
    return None


class Session:
    """One switch's NETCONF session, opened on first use and after a failure; with the login of
    the moment (the GUI's settings may change it)."""

    def __init__(self, target: Target, login: str, password: str, timeout: float = 30):
        self.target, self.login, self.password, self.timeout = target, login, password, timeout
        self.conn = None
        self.opened = 0

    def _conn(self):
        if self.conn is None or not self.conn.connected:
            self.login, self.password = shared_login() or (self.login, self.password)
            self.conn = connect(self.target, self.login, self.password, self.timeout)
            self.opened += 1
        return self.conn

    def get(self, filter_xml: str) -> DeviceState:
        return parse(self._conn().get(filter=("subtree", filter_xml)).data_xml)

    def get_control(self) -> control.ControlState:
        """The control-plane queries. A query this switch refuses (rpc-error: a model without
        M-LAG, say) leaves out that part only; a transport failure fails the poll."""
        replies = {}
        for name, subtree in control.QUERIES.items():
            try:
                replies[name] = self._conn().get(filter=("subtree", subtree)).data_xml
            except RPCError:
                replies[name] = None
        return control.parse(replies)

    def get_hardware(self, group: str) -> hardware.HardwareState:
        """One hardware group, refused queries left out as for get_control."""
        from lxml import etree

        replies = {}
        for name in hardware.GROUPS[group]:
            try:
                if name == "temperature":
                    replies[name] = self._conn().dispatch(etree.fromstring(hardware.TEMPERATURE_RPC)).xml
                else:
                    replies[name] = self._conn().get(filter=("subtree", hardware.QUERIES[name])).data_xml
            except RPCError:
                replies[name] = None
        return hardware.parse(replies)

    def drop(self) -> None:
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close_session()
            except Exception:   # it is being dropped because it failed
                pass


def connect(target: Target, login: str, password: str, timeout: float = 30):
    """A NETCONF session, only if the switch presents a pinned host key (`target.check`, see
    orchestrator.hostkeys.netconf_check; it runs before the login is sent)."""
    return manager.connect(
        host=target.address,
        port=hostkeys.NETCONF_PORT,
        username=login,
        password=password,
        hostkey_verify=True,
        unknown_host_cb=target.check,
        allow_agent=False,
        look_for_keys=False,
        device_params={"name": "default"},
        timeout=timeout,
    )


async def active_devices(netbox: NetBoxClient) -> dict[str, Target | None]:
    """name -> Target of every active device with a primary IPv4; None when it is not pinned."""
    devices = await netbox.find("dcim/devices", status="active", has_primary_ip="true")
    found = {}
    for d in devices:
        if not d.get("primary_ip4"):
            continue
        check = hostkeys.netconf_check(d)
        pins = tuple(sorted(hostkeys.pins(d)[hostkeys.NETCONF_PORT]))
        found[d["name"]] = Target(hostkeys.address(d), pins, check) if check else None
    return found


async def poll_device(name: str, session: Session, metrics: FabricCollector, counter_interval: float,
                      state_every: int, retry_after: float, attempts: int | None = None,
                      control_every: int | None = None, hardware_every: int | None = None) -> None:
    """Poll one switch until cancelled (or `attempts` polls, for tests): counters every tick,
    state and LLDP every state_every ticks, control-plane state every control_every ticks,
    a hardware group every hardware_every ticks, the groups in turn."""
    tick, next_at, done = 0, time.monotonic(), 0
    refused: set[str] = set()   # control-plane and hardware queries this switch refuses, logged once
    while attempts is None or done < attempts:
        done += 1
        kind = FULL if tick % state_every == 0 else FAST
        started, opened = time.monotonic(), session.opened
        try:
            state = await asyncio.to_thread(session.get, STATE_FILTER if kind == FULL else COUNTERS_FILTER)
            took = time.monotonic() - started   # this poll alone: the control poll after it is timed apart
            if control_every and tick % control_every == 0:
                control_started = time.monotonic()
                plane = await asyncio.to_thread(session.get_control)
                metrics.record_control(name, plane, time.monotonic() - control_started)
                refused = _log_refused(name, refused, control.QUERIES, plane.missing)
            if hardware_every and tick % hardware_every == 0:
                group = list(hardware.GROUPS)[(tick // hardware_every) % len(hardware.GROUPS)]
                hardware_started = time.monotonic()
                parts = await asyncio.to_thread(session.get_hardware, group)
                metrics.record_hardware(name, group, parts, time.monotonic() - hardware_started)
                refused = _log_refused(name, refused, hardware.GROUPS[group], parts.missing)
        except Exception as exc:   # any transport or RPC failure marks the device down
            log.warning("%s: %s poll failed: %s: %s", name, kind, type(exc).__name__, exc)
            await asyncio.to_thread(session.drop)
            metrics.record_failure(name, str(exc))
            tick = 0   # after a failure, start again with a full state poll
            next_at = time.monotonic() + retry_after
        else:
            if session.opened > opened and opened > 0:
                metrics.record_reconnect(name)
            metrics.record_success(name, state, kind, took)
            if kind == FULL:
                log.info("%s: %d interfaces, %d LLDP neighbours", name, len(state.oper_up), len(state.neighbors))
            tick += 1
            next_at = max(next_at + counter_interval, time.monotonic())   # never overlap
        await asyncio.sleep(max(0.0, next_at - time.monotonic()))


def _log_refused(name: str, refused: set[str], asked, missing: set[str]) -> set[str]:
    """Log once when the queries this switch refuses change; `asked`: the queries of this poll."""
    now = (refused - set(asked)) | missing
    if missing - refused:
        log.info("%s: no %s state on this switch (refused): left out", name, ", ".join(sorted(missing - refused)))
    return now


class Collector:
    """Keeps one polling task per pinned active switch, in step with NetBox."""

    def __init__(self, metrics: FabricCollector, login: str, password: str, counter_interval: float,
                 state_interval: float, session_factory=Session, control_interval: float = 0,
                 hardware_interval: float = 0):
        self.metrics, self.login, self.password = metrics, login, password
        self.counter_interval, self.state_interval = counter_interval, state_interval
        self.state_every = max(1, round(state_interval / counter_interval))
        # Control polls follow state polls: a multiple of the state interval.
        self.control_every = self.state_every * max(1, round(control_interval / state_interval)) if control_interval else None
        # Hardware groups take turns in state slots: each group every hardware_interval.
        groups = len(hardware.GROUPS)
        self.hardware_every = (self.state_every * max(1, round(hardware_interval / state_interval / groups))
                               if hardware_interval else None)
        self.session_factory = session_factory
        self.tasks: dict[str, tuple[Target, asyncio.Task, Session]] = {}

    async def refresh(self, devices: dict[str, Target | None]) -> None:
        self.metrics.forget_others(set(devices))
        for name in list(self.tasks):
            target, task, session = self.tasks[name]
            if devices.get(name) != target:   # gone, unpinned, readdressed or re-pinned
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.to_thread(session.drop)
                del self.tasks[name]
        for name, target in devices.items():
            if target is None:
                log.warning("%s: no pinned NETCONF host key; not polled (python -m orchestrator.hostkeys pin %s)",
                            name, name)
                self.metrics.record_failure(name, "no pinned NETCONF host key")
            elif name not in self.tasks:
                session = self.session_factory(target, self.login, self.password)
                task = asyncio.create_task(poll_device(name, session, self.metrics, self.counter_interval,
                                                       self.state_every, self.state_interval,
                                                       control_every=self.control_every,
                                                       hardware_every=self.hardware_every))
                self.tasks[name] = (target, task, session)

    async def stop(self) -> None:
        await self.refresh({})


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("ncclient").setLevel(logging.WARNING)   # it logs every RPC at INFO
    login, password = shared_login() or (os.environ.get("COLLECTOR_LOGIN"), os.environ.get("COLLECTOR_PASSWORD"))
    if not login or not password:
        log.error("no collector login: set it in the GUI (Admin > Settings) or COLLECTOR_LOGIN/COLLECTOR_PASSWORD")
        return 1
    state_interval = float(os.environ.get("COLLECTOR_INTERVAL", "25"))
    counter_interval = float(os.environ.get("COLLECTOR_COUNTER_INTERVAL", "5"))
    settings = Settings.from_env()
    netbox = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)

    metrics = FabricCollector()
    REGISTRY.register(metrics)
    start_http_server(int(os.environ.get("COLLECTOR_PORT", "9108")))
    control_interval = float(os.environ.get("COLLECTOR_CONTROL_INTERVAL", "25"))
    hardware_interval = float(os.environ.get("COLLECTOR_HARDWARE_INTERVAL", "50"))
    collector = Collector(metrics, login, password, counter_interval, state_interval, control_interval=control_interval,
                          hardware_interval=hardware_interval)
    while True:
        try:
            await collector.refresh(await active_devices(netbox))
        except NetBoxError as exc:   # keep polling the devices already known
            log.error("cannot list devices from NetBox: %s", exc)
        await asyncio.sleep(state_interval)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
