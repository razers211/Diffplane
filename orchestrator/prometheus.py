"""Read the collector's metrics from Prometheus."""

import re

import httpx

from orchestrator.timing import measure

from orchestrator.topology import Telemetry


class PrometheusError(Exception):
    pass


class PrometheusClient:
    def __init__(self, url: str, timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport)

    async def query(self, expr: str) -> list[tuple[dict[str, str], float, float]]:
        """Instant query: [(labels, sample timestamp, value)]."""
        try:
            with measure("prometheus"):
                response = await self._http.get("/api/v1/query", params={"query": expr})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PrometheusError(f"Prometheus query failed: {exc}") from exc
        if body.get("status") != "success":
            raise PrometheusError(f"Prometheus query failed: {body.get('error', body)}")
        return [(r["metric"], float(r["value"][0]), float(r["value"][1])) for r in body["data"]["result"]]

    async def query_range(self, expr: str, start: float, end: float, step: float) -> list[tuple[dict[str, str], list[tuple[float, float]]]]:
        """Range query: [(labels, [(timestamp, value)])]."""
        try:
            with measure("prometheus"):
                response = await self._http.get("/api/v1/query_range",
                                                params={"query": expr, "start": start, "end": end, "step": step})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PrometheusError(f"Prometheus query failed: {exc}") from exc
        if body.get("status") != "success":
            raise PrometheusError(f"Prometheus query failed: {body.get('error', body)}")
        return [(r["metric"], [(float(t), float(v)) for t, v in r["values"]]) for r in body["data"]["result"]]

    async def firing_alerts(self) -> list[dict]:
        """Alerts firing now (deploy/prometheus/alerts.yml), with the time each became active."""
        since = {
            (m.get("alertname"), m.get("device")): v
            for m, _, v in await self.query("ALERTS_FOR_STATE")
        }
        alerts = []
        for m, _, _ in await self.query('ALERTS{alertstate="firing"}'):
            key = (m.get("alertname"), m.get("device"))
            alerts.append({"alert": m.get("alertname"), "device": m.get("device"),
                           "severity": m.get("severity"), "since": since.get(key)})
        return sorted(alerts, key=lambda a: (a["since"] or 0, a["alert"] or ""))

    @staticmethod
    def _devices_match(devices: list[str]) -> str:
        """A label matcher for these devices; names are checked, so only "." needs escaping
        (doubled inside a PromQL string)."""
        for device in devices:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", device):
                raise PrometheusError(f"invalid device name: {device!r}")
        return 'device=~"' + "|".join(d.replace(".", "\\\\.") for d in devices) + '"'

    async def fabric_traffic(self, devices: list[str]) -> dict[str, dict[str, dict[str, float]]]:
        """device -> interface -> in/out bits per second now, for every physical port of `devices`."""
        if not devices:
            return {}
        match = self._devices_match(devices)
        result: dict[str, dict[str, dict[str, float]]] = {}
        for name in ("in", "out"):
            for labels, _, value in await self.query(f"irate(fabric_interface_{name}_octets_total{{{match}}}[30s]) * 8"):
                result.setdefault(labels["device"], {}).setdefault(labels["interface"], {})[f"{name}_bps"] = value
        return result

    async def interface_history(self, device: str, interface: str, minutes: int,
                                now: float | None = None) -> dict:
        """One port's in/out bits per second over the last `minutes`, about 120 points."""
        import time

        self._devices_match([device])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/.:_-]*", interface):
            raise PrometheusError(f"invalid interface name: {interface!r}")
        end = now or time.time()
        step = max(5, minutes * 60 // 120)
        # Each point averages its own step (at least 30 s: irate needs two 5 s samples).
        window = f"{max(30, step)}s"
        sel = f'device="{device}",interface="{interface}"'
        series = {}
        for name in ("in", "out"):
            found = await self.query_range(f"rate(fabric_interface_{name}_octets_total{{{sel}}}[{window}]) * 8",
                                           end - minutes * 60, end, step)
            series[name] = dict(found[0][1]) if found else {}
        times = sorted(set(series["in"]) | set(series["out"]))
        return {"step": step, "points": [{"t": t, "in_bps": series["in"].get(t), "out_bps": series["out"].get(t)}
                                         for t in times]}

    async def busiest_ports(self, devices: list[str], count: int = 5, minutes: int = 15,
                            now: float | None = None) -> list[dict]:
        """The `count` physical ports of `devices` with the most traffic now (in + out, bits/s),
        each with its in/out rates and the total over the last `minutes` (one point per 30 s)."""
        import time

        if not devices:
            return []
        match = self._devices_match(devices)
        rate = lambda name, sel: f"irate(fabric_interface_{name}_octets_total{{{sel}}}[30s]) * 8"
        top = await self.query(f"topk({int(count)}, {rate('in', match)} + {rate('out', match)})")
        end = now or time.time()
        ports = []
        for labels, _, total in sorted(top, key=lambda t: -t[2]):
            sel = f'device="{labels["device"]}",interface="{labels["interface"]}"'
            values = {name: await self.query(rate(name, sel)) for name in ("in", "out")}
            history = await self.query_range(f"{rate('in', sel)} + {rate('out', sel)}", end - minutes * 60, end, 30)
            ports.append({
                "device": labels["device"], "interface": labels["interface"], "bps": total,
                "in_bps": values["in"][0][2] if values["in"] else None,
                "out_bps": values["out"][0][2] if values["out"] else None,
                "history": [v for _, v in history[0][1]] if history else [],
            })
        return ports

    async def telemetry(self, max_age: float) -> Telemetry:
        """Collector state; a device counts as collected if polled successfully within max_age seconds."""
        up = {m["device"]: v for m, _, v in await self.query("fabric_collector_device_up")}
        last = {
            m["device"]: (ts, v)
            for m, ts, v in await self.query("fabric_collector_last_success_timestamp_seconds")
        }
        collected = {
            device for device, value in up.items()
            if value == 1 and device in last and last[device][0] - last[device][1] <= max_age
        }
        telemetry = Telemetry(collected=collected)
        for m, _, v in await self.query("fabric_interface_oper_up"):
            telemetry.oper_up[(m["device"], m["interface"])] = v == 1
        for m, _, _ in await self.query("fabric_lldp_neighbor_info"):
            telemetry.lldp.setdefault((m["device"], m["interface"]), []).append(
                (m["neighbor_system_name"], m["neighbor_port_id"])
            )
        for neighbours in telemetry.lldp.values():
            neighbours.sort()
        return telemetry

    async def device_state(self, device: str) -> tuple[float | None, bool, dict[str, bool]]:
        """(last successful poll time, last poll ok, interface -> oper up) for one device."""
        label = f'{{device="{device}"}}'
        last = await self.query(f"fabric_collector_last_success_timestamp_seconds{label}")
        up = await self.query(f"fabric_collector_device_up{label}")
        oper = await self.query(f"fabric_interface_oper_up{label}")
        return (
            last[0][2] if last else None,
            bool(up and up[0][2] == 1),
            {m["interface"]: v == 1 for m, _, v in oper},
        )

    async def interface_traffic(self, device: str) -> dict[str, dict[str, float]]:
        """interface -> in/out bits per second (last two samples, 5 s apart) and errors/discards
        (last 5 minutes)."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", device):
            raise PrometheusError(f"invalid device name: {device!r}")
        label = f'{{device="{device}"}}'
        queries = {
            # Counters every 5 s: irate over the last two samples follows a change within
            # one interval; 30 s of window tolerates a few missed scrapes.
            "in_bps": f"irate(fabric_interface_in_octets_total{label}[30s]) * 8",
            "out_bps": f"irate(fabric_interface_out_octets_total{label}[30s]) * 8",
            **{name: f"increase(fabric_interface_{name}_total{label}[5m])"
               for name in ("in_errors", "out_errors", "in_discards", "out_discards")},
        }
        result: dict[str, dict[str, float]] = {}
        for name, expr in queries.items():
            for labels, _, value in await self.query(expr):
                result.setdefault(labels["interface"], {})[name] = value
        return result

    async def aclose(self) -> None:
        await self._http.aclose()
