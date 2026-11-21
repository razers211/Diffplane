"""Measure what fast interface polling costs a switch, read-only.

Keeps ONE NETCONF session open (the switch resets rapid reconnects), sends the collector's
counters-only <get> every --interval seconds for --minutes, and reports:

  - poll durations and failures, and reconnects of the session;
  - how often the switch refreshes mib-statistics: MEth0/0/0 carries this very session,
    so its counters would change at every poll if the switch refreshed them continuously;
  - the switch CPU and memory from huawei-cpu-memory, every 10 s (board, each core).

    python -m collector.measure leaf1 --interval 5 --minutes 10
    python -m collector.measure leaf1 --baseline --minutes 2      # CPU without our polls
    python -m collector.measure leaf1 --interval 5 --main-only    # without sub-interfaces

Runs in the collector container (COLLECTOR_LOGIN/COLLECTOR_PASSWORD, the device's pinned
NETCONF host key from NetBox). Only <get> is sent.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
import xml.etree.ElementTree as ET

from ncclient import manager

from collector.probe import target
from collector.state import parse, subtree

# Board CPU, per-core CPU and memory only: the per-process lists are large (huawei-cpu-memory,
# structure read from a test switch's reply).
CPU_MEMORY = "urn:huawei:yang:huawei-cpu-memory"
CPU_FILTER = (f'<cpu-memory xmlns="{CPU_MEMORY}"><board-cpu-infos/><board-cpu-core-infos/>'
              "<board-memory-infos/></cpu-memory>")
CPU_EVERY = 10.0
WATCHED = "MEth0/0/0"


def connect(host: str, check, login: str, password: str):
    return manager.connect(host=host, port=830, username=login, password=password, hostkey_verify=True,
                           unknown_host_cb=check, allow_agent=False, look_for_keys=False,
                           device_params={"name": "default"}, timeout=30)


def cpu_sample(xml: str) -> dict[str, float]:
    """{"system": %, "core 0": %, ..., "memory": %} from a huawei-cpu-memory reply."""
    root = ET.fromstring(xml)
    ns = {"c": CPU_MEMORY}
    sample = {}
    for info in root.iterfind(".//c:board-cpu-info", ns):
        sample[f"system (slot {info.findtext('c:slot-id', '', ns)})"] = float(info.findtext("c:system-cpu-usage", "0", ns))
    for core in root.iterfind(".//c:board-cpu-core-info", ns):
        sample[f"core {core.findtext('c:core-id', '?', ns)}"] = float(core.findtext("c:cpu-core-usage", "0", ns))
    for memory in root.iterfind(".//c:board-memory-info", ns):
        sample[f"memory (slot {memory.findtext('c:slot-id', '', ns)})"] = float(memory.findtext("c:os-memory-usage", "0", ns))
    return sample


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def measure(host: str, check, login: str, password: str, interval: float, minutes: float,
            counters: bool = True, main_only: bool = False) -> int:
    durations, failures, reconnects = [], [], 0
    changes, last_value, polls = [], None, 0
    cpu: dict[str, list[float]] = {}
    session = connect(host, check, login, password)
    next_cpu = time.monotonic()
    stop = time.monotonic() + minutes * 60
    next_poll = time.monotonic()
    while time.monotonic() < stop:
        time.sleep(max(0.0, next_poll - time.monotonic()))
        next_poll += interval
        started = time.monotonic()
        try:
            if not session.connected:
                session = connect(host, check, login, password)
                reconnects += 1
            if counters:
                state = parse(session.get(filter=("subtree", subtree(lldp=False, main_only=main_only))).data_xml)
        except Exception as exc:   # count it, keep measuring
            failures.append(f"{time.strftime('%H:%M:%S')} {type(exc).__name__}: {exc}")
            continue
        if counters:
            durations.append(time.monotonic() - started)
            polls += 1
            value = state.counters.get(WATCHED, {}).get("out_octets")
            interfaces = len(state.counters)
            if value is not None and value != last_value:
                changes.append(time.monotonic())
                last_value = value
        if time.monotonic() >= next_cpu:
            next_cpu += CPU_EVERY
            try:
                for name, number in cpu_sample(session.get(filter=("subtree", CPU_FILTER)).data_xml).items():
                    cpu.setdefault(name, []).append(number)
            except Exception as exc:
                failures.append(f"{time.strftime('%H:%M:%S')} cpu-memory {type(exc).__name__}: {exc}")
    session.close_session()

    if not counters:
        print(f"CPU baseline: no counter polls, CPU sampled every {CPU_EVERY:g} s for {minutes:g} min")
    else:
        print(f"polls: {polls} ok, {len(failures)} failed, {reconnects} reconnects, every {interval:g} s "
              f"for {minutes:g} min, {interfaces if polls else 0} interfaces"
              f"{' (main interfaces only)' if main_only else ''}")
    if durations:
        print(f"poll duration: median {statistics.median(durations):.2f} s, "
              f"p95 {percentile(durations, 0.95):.2f} s, max {max(durations):.2f} s")
    if not counters:
        pass
    elif len(changes) > 2:
        gaps = [b - a for a, b in zip(changes, changes[1:])]
        print(f"{WATCHED} out-octets changed {len(changes)} times in {polls} polls: "
              f"median {statistics.median(gaps):.1f} s between changes (min {min(gaps):.1f}, max {max(gaps):.1f})")
    else:
        print(f"{WATCHED} out-octets changed {len(changes)} times: not enough to estimate the refresh")
    for line in failures[:10]:
        print("failure:", line)
    for name, values in sorted(cpu.items()):
        print(f"{name} usage %: min {min(values):g}, avg {statistics.mean(values):.1f}, max {max(values):g}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m collector.measure", description=__doc__.splitlines()[0])
    parser.add_argument("device", help="NetBox device name")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--baseline", action="store_true", help="no counter polls: CPU only")
    parser.add_argument("--main-only", action="store_true", help="leave out sub-interfaces")
    args = parser.parse_args()
    login, password = os.environ.get("COLLECTOR_LOGIN"), os.environ.get("COLLECTOR_PASSWORD")
    if not login or not password:
        print("COLLECTOR_LOGIN and COLLECTOR_PASSWORD are required", file=sys.stderr)
        return 1
    host, check = asyncio.run(target(args.device))
    return measure(host, check, login, password, args.interval, args.minutes,
                   counters=not args.baseline, main_only=args.main_only)


if __name__ == "__main__":
    sys.exit(main())
