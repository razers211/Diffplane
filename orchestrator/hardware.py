"""A switch's hardware for the GUI: what the collector put in Prometheus (collector/hardware.py),
shaped per switch, with problems listed.

Thresholds are the switch's or the module's own. An optic's level is a problem
only on a port in use (cabled, a trunk member, a service port or carrying segments:
`faceplate.in_use`); unused modules are shown with their readings, never as a problem.
"""

import asyncio
import re

from orchestrator import faceplate as fp
from orchestrator.prometheus import PrometheusError

DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SERIES = "fabric_(optic_.*|fan_.*|power_module_.*|cpu_.*|memory_.*|temperature_.*|hardware_query_refused)"
OPTIC_MEASURES = (("rx_power", "received", "dBm"), ("tx_power", "sent", "dBm"), ("bias", "bias", "mA"),
                  ("temperature", "temperature", "°C"), ("voltage", "voltage", "V"))


def _level(value: float, limits: dict[str, float]) -> str | None:
    """`alarm`, `warning` or None for a reading against the module's own limits."""
    for level in ("alarm", "warn"):
        high, low = limits.get(f"high_{level}"), limits.get(f"low_{level}")
        if (high is not None and value > high) or (low is not None and value < low):
            return "alarm" if level == "alarm" else "warning"
    return None


def _optic(port: str, rows: dict[str, list], in_use: bool) -> dict:
    info = rows["info"][0] if rows["info"] else {}
    limits: dict[str, dict[str, float]] = {}
    for labels, value in rows["threshold"]:
        limits.setdefault(labels["measure"], {})[labels["level"]] = value
    readings: list[tuple[str, str | None, float]] = []   # (measure, channel, value)
    channels: dict[str, dict] = {}
    for measure in ("rx_power", "tx_power", "bias"):
        for labels, value in rows[measure]:
            channels.setdefault(labels["channel"], {"channel": labels["channel"]})[measure] = value
            readings.append((measure, labels["channel"], value))
    for measure in ("temperature", "voltage"):
        for _, value in rows[measure]:
            readings.append((measure, None, value))
    worst, reasons = None, []
    for measure, channel, value in readings:
        level = _level(value, limits.get(measure, {}))
        if level is None:
            continue
        _, word, unit = next(m for m in OPTIC_MEASURES if m[0] == measure)
        where = f" (channel {channel})" if channel is not None and len(channels) > 1 else ""
        reasons.append(f"{word} {value:g} {unit}{where} past the module's {level} threshold")
        worst = "alarm" if "alarm" in (worst, level) else level
    single = {m: (rows[m][0][1] if rows[m] else None) for m in ("temperature", "voltage")}
    return {"port": port, "vendor": info.get("vendor"), "type": info.get("type"), "part": info.get("part"),
            "serial": info.get("serial"), **single, "channels": [channels[c] for c in sorted(channels)],
            "thresholds": limits, "in_use": in_use, "level": (worst or "ok") if in_use else "unused",
            "reasons": reasons if in_use else []}


def shape(device: str, series: list[tuple[dict, float, float]], in_use: set[str]) -> dict:
    """The collector's hardware series of one switch, as the GUI shows them, with problems listed."""
    by_name: dict[str, list[tuple[dict, float]]] = {}
    for labels, _, value in series:
        by_name.setdefault(labels.get("__name__", "").removeprefix("fabric_"), []).append((labels, value))

    def rows(name: str) -> list[tuple[dict, float]]:
        return by_name.get(name, [])

    problems: list[str] = []
    optic_rows: dict[str, dict[str, list]] = {}
    families = {"info": "optic_info", "threshold": "optic_threshold", "rx_power": "optic_rx_power_dbm",
                "tx_power": "optic_tx_power_dbm", "bias": "optic_bias_milliamperes",
                "temperature": "optic_temperature_celsius", "voltage": "optic_voltage_volts"}
    for key, name in families.items():
        for labels, value in rows(name):
            target = optic_rows.setdefault(labels["interface"], {k: [] for k in families})
            target[key].append(labels if key == "info" else (labels, value))
    optics = [_optic(port, r, port in in_use) for port, r in sorted(optic_rows.items(), key=lambda x: _port_key(x[0]))]
    for o in optics:
        problems += [f"optic in {o['port']}: {reason}" for reason in o["reasons"]]

    def per(name: str, label: str) -> dict[str, float]:
        return {labels[label]: value for labels, value in rows(name)}

    speed, rpm = per("fan_speed_percent", "fan"), per("fan_rpm", "fan")
    fans = [{"fan": fan, "present": present == 1, "speed": speed.get(fan), "rpm": rpm.get(fan)}
            for fan, present in sorted(per("fan_present", "fan").items())]
    problems += [f"fan {f['fan']} is missing" for f in fans if not f["present"]]
    status = {labels["module"]: labels["status"] for labels, _ in rows("power_module_info")}
    watts_in, watts_out = per("power_module_input_watts", "module"), per("power_module_output_watts", "module")
    power = [{"module": m, "ok": up == 1, "status": status.get(m), "input": watts_in.get(m), "output": watts_out.get(m)}
             for m, up in sorted(per("power_module_up", "module").items())]
    problems += [f"power module {p['module']} is {p['status'] or 'not normal'}" for p in power if not p["ok"]]
    load = {}
    for kind in ("cpu", "memory"):
        threshold = per(f"{kind}_overload_threshold_percent", "slot")
        load[kind] = [{"slot": slot, "usage": usage, "threshold": threshold.get(slot),
                       "ok": threshold.get(slot) is None or usage < threshold[slot]}
                      for slot, usage in sorted(per(f"{kind}_usage_percent", "slot").items())]
        problems += [f"{kind.upper() if kind == 'cpu' else kind} of slot {r['slot']} at {r['usage']:g} %, over the "
                     f"switch's threshold of {r['threshold']:g} %" for r in load[kind] if not r["ok"]]
    normal = {(l["slot"], l["sensor"]): v == 1 for l, v in rows("temperature_sensor_normal")}
    limits: dict[tuple, dict[str, float]] = {}
    for labels, value in rows("temperature_threshold_celsius"):
        limits.setdefault((labels["slot"], labels["sensor"]), {})[labels["level"]] = value
    sensors = []
    for labels, celsius in sorted(rows("temperature_celsius"), key=lambda x: (x[0]["slot"], x[0]["sensor"])):
        key = (labels["slot"], labels["sensor"])
        over = [level for level in ("fatal", "major", "minor") if key in limits and level in limits[key]
                and celsius >= limits[key][level]]
        ok = normal.get(key, True) and not over
        sensors.append({"slot": key[0], "sensor": key[1], "celsius": celsius, "normal": normal.get(key),
                        "thresholds": limits.get(key, {}), "ok": ok})
        if not ok:
            what = f"at its {over[0]} threshold" if over else "not Normal"
            problems.append(f"temperature {key[1]} in slot {key[0]}: {celsius:g} °C, {what}")
    return {"device": device, "optics": optics, "fans": fans, "power": power, "cpu": load["cpu"],
            "memory": load["memory"], "sensors": sensors,
            "refused": sorted(l.get("query") for l, _ in rows("hardware_query_refused")),
            "collected": bool(series), "problems": problems}


def _port_key(name: str) -> tuple:
    """10GE1/0/2 before 10GE1/0/10; 10GE before 100GE."""
    speed, _, rest = name.partition("GE")
    return (int(speed) if speed.isdigit() else 0, *(int(n) for n in re.findall(r"\d+", rest)))


async def device_hardware(nb, prometheus, device: str) -> dict:
    if not DEVICE.fullmatch(device):   # it goes into a PromQL string
        raise PrometheusError(f"invalid device name: {device!r}")
    series, plate = await asyncio.gather(prometheus.query(f'{{__name__=~"{SERIES}",device="{device}"}}'),
                                         fp.faceplate(nb, device))
    return shape(device, series, {p["name"] for p in plate["ports"] if p["in_use"]})
