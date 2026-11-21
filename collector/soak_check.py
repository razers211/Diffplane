"""Collector soak check, read-only: `python -m collector.soak_check <device> [hours]`.

Asks Prometheus (PROMETHEUS_URL) about the last `hours` (default 2) of one switch's polls:
the window must be whole (Prometheus scraped the collector all along), with 0 failed polls, 0 reconnects, every counters poll shorter than the interval, and
about one poll per interval (4 counters polls and 1 state poll in each 25 s).
also one control-plane poll per COLLECTOR_CONTROL_INTERVAL (25 s), each shorter than it.
also one read of each hardware group (environment, optics) per COLLECTOR_HARDWARE_INTERVAL (50 s),
and a state slot (its slowest state, control and hardware polls added up) shorter than COLLECTOR_INTERVAL.
Prometheus samples the last poll's duration every 5 s, so the slowest poll is the slowest
one sampled.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

INTERVAL = float(os.environ.get("COLLECTOR_COUNTER_INTERVAL", "5"))
CONTROL_INTERVAL = float(os.environ.get("COLLECTOR_CONTROL_INTERVAL", "25"))
STATE_INTERVAL = float(os.environ.get("COLLECTOR_INTERVAL", "25"))
HARDWARE_INTERVAL = float(os.environ.get("COLLECTOR_HARDWARE_INTERVAL", "50"))
HARDWARE_GROUPS = ("environment", "optics")   # collector.hardware.GROUPS
TOLERANCE = 0.98   # increase() extrapolates at the window's edges


def queries(device: str, hours: float) -> dict[str, str]:
    d, w = json.dumps(device), f"{int(hours * 3600)}s"
    return {
        "scrapes": f"count_over_time(fabric_collector_device_up{{device={d}}}[{w}])",
        "polls": f"sum(increase(fabric_collector_polls_total{{device={d}}}[{w}]))",
        "failures": f"sum(increase(fabric_collector_poll_failures_total{{device={d}}}[{w}]))",
        "reconnects": f"sum(increase(fabric_collector_reconnects_total{{device={d}}}[{w}]))",
        "slowest": f"max_over_time(fabric_collector_poll_duration_seconds{{device={d},poll=\"counters\"}}[{w}])",
        "control_polls": f"sum(increase(fabric_collector_polls_total{{device={d},poll=\"control\"}}[{w}]))",
        "slowest_control": f"max_over_time(fabric_collector_poll_duration_seconds{{device={d},poll=\"control\"}}[{w}])",
        "slowest_state": f"max_over_time(fabric_collector_poll_duration_seconds{{device={d},poll=\"state\"}}[{w}])",
        **{f"{g}_polls": f"sum(increase(fabric_collector_polls_total{{device={d},poll=\"{g}\"}}[{w}]))" for g in HARDWARE_GROUPS},
        **{f"slowest_{g}": f"max_over_time(fabric_collector_poll_duration_seconds{{device={d},poll=\"{g}\"}}[{w}])"
           for g in HARDWARE_GROUPS},
    }


def verdict(values: dict[str, float | None], hours: float, interval: float = INTERVAL,
            control_interval: float = CONTROL_INTERVAL, hardware_interval: float = HARDWARE_INTERVAL,
            state_interval: float = STATE_INTERVAL) -> list[tuple[str, bool, str]]:
    """(check, passed, detail) for each condition (control polls, hardware)."""
    expected = hours * 3600 / interval
    expected_control = hours * 3600 / control_interval if control_interval else 0

    def got(name):
        return values.get(name) if values.get(name) is not None else float("nan")

    return [
        ("window whole (awake)", got("scrapes") >= expected * TOLERANCE,
         f"{got('scrapes'):.0f} scrapes of {expected:.0f}"),
        ("one poll per interval", got("polls") >= expected * TOLERANCE, f"{got('polls'):.0f} polls of {expected:.0f}"),
        ("0 failed polls", got("failures") < 0.5, f"{got('failures'):.1f}"),
        ("0 reconnects", got("reconnects") < 0.5, f"{got('reconnects'):.1f}"),
        ("counters polls under the interval", got("slowest") < interval, f"slowest {got('slowest'):.2f} s"),
    ] + ([
        ("one control poll per control interval", got("control_polls") >= expected_control * TOLERANCE,
         f"{got('control_polls'):.0f} of {expected_control:.0f}"),
        ("control polls under their interval", got("slowest_control") < control_interval,
         f"slowest {got('slowest_control'):.2f} s"),
    ] if control_interval else []) + ([
        (f"one {g} read per hardware interval", got(f"{g}_polls") >= hours * 3600 / hardware_interval * TOLERANCE,
         f"{got(f'{g}_polls'):.0f} of {hours * 3600 / hardware_interval:.0f}") for g in HARDWARE_GROUPS
    ] + [
        ("state slot under the state interval", _slot(values) < state_interval,
         f"at most {_slot(values):.2f} s (slowest state + control + hardware)"),
    ] if hardware_interval else [])


def _slot(values: dict[str, float | None]) -> float:
    """The longest a state slot can have taken: its slowest polls added up (an upper bound)."""
    parts = [values.get("slowest_state"), values.get("slowest_control"),
             max((values.get(f"slowest_{g}") or 0.0) for g in HARDWARE_GROUPS)]
    return sum(parts) if None not in parts[:1] else float("nan")


def query(base: str, expr: str) -> float | None:
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    # The operator's own Prometheus URL, given on the command line.
    with urllib.request.urlopen(url, timeout=30) as response:  # nosec B310  # nosemgrep
        result = json.load(response)["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def main(argv: list[str]) -> int:
    if not argv or len(argv) > 2:
        print(__doc__.strip().splitlines()[0])
        return 2
    device, hours = argv[0], float(argv[1]) if len(argv) > 1 else 2.0
    base = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
    values = {name: query(base, expr) for name, expr in queries(device, hours).items()}
    results = verdict(values, hours)
    for check, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {check}: {detail}")
    ok = all(passed for _, passed, _ in results)
    print(f"Soak check, {hours:g} h window for {device}: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
