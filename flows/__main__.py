"""The flows service: `python -m flows`.

Runs GoFlow2 (built into the image) on UDP `FLOWS_PORT` (2055), reads the JSON record it prints
per flow, keeps those of switches the platform knows (by MEth address), names their port and
segments, scales them by the sampling rate and writes them to ClickHouse every 5 seconds. Its
context (switches, ifIndexes, edge ports, segment subnets) is read again every minute. Nothing is
sent to any switch.
"""

import asyncio
import ipaddress
import json
import logging
import os
import time

from flows.records import Context, row
from flows.store import ClickHouse

log = logging.getLogger("flows")
FLUSH_SECONDS, CONTEXT_SECONDS = 5, 60


async def load_context(nb, prometheus) -> Context:
    from orchestrator.inventory import SITE as INVENTORY
    from orchestrator.routing_intent import RoutingStore, status_of

    ctx = Context()
    devices = [d for d in await nb.find("dcim/devices") if status_of(d) == "active"]
    fabrics = sorted({(d.get("site") or {}).get("slug") for d in devices} - {None, INVENTORY})
    for d in devices:
        address = ((d.get("primary_ip4") or d.get("primary_ip") or {}).get("address") or "").split("/")[0]
        if address:
            ctx.exporters[address] = (d["name"], (d.get("site") or {}).get("slug"))
    store = RoutingStore(nb)
    for fabric in fabrics:
        (services, segments), interfaces = await asyncio.gather(store.fabric_state(fabric),
                                                                nb.find("dcim/interfaces", site=fabric))
        for s in segments:
            for a in s.get("attachments") or []:
                ctx.edge.add((a["device"], a["interface"]))
            gateway = (s.get("routing") or {}).get("gateway")
            if gateway:
                ctx.subnets.setdefault(fabric, []).append((ipaddress.ip_interface(gateway).network, s["name"]))
        for service in services:
            for leaf in service.get("devices") or [service["device"]]:
                ctx.edge.add((leaf, service["port"]))
        for i in interfaces:   # a trunk's members, in case a record names the member it came in on
            lag = (i.get("lag") or {}).get("name") if isinstance(i.get("lag"), dict) else None
            device = (i.get("device") or {}).get("name")
            if lag and (device, lag) in ctx.edge:
                ctx.edge.add((device, i["name"]))
    for labels, _, value in await prometheus.query("fabric_interface_ifindex"):
        ctx.ifnames[(labels["device"], int(value))] = labels["interface"]
    return ctx


async def run() -> None:
    from orchestrator.config import Settings
    from orchestrator.netbox import NetBoxClient
    from orchestrator.prometheus import PrometheusClient

    settings = Settings.from_env()
    nb = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
    prometheus = PrometheusClient(settings.prometheus_url)
    clickhouse = ClickHouse(os.environ.get("CLICKHOUSE_URL", "http://clickhouse:8123"),
                            os.environ.get("CLICKHOUSE_USER", "flows"), os.environ["CLICKHOUSE_PASSWORD"],
                            cluster=os.environ.get("CLICKHOUSE_CLUSTER") or None)   # replicated on the HA servers
    while True:   # ClickHouse may still be starting
        try:
            await clickhouse.create()
            break
        except Exception as exc:
            log.warning("ClickHouse not ready (%s); again in 5 s", exc)
            await asyncio.sleep(5)
    port = int(os.environ.get("FLOWS_PORT", 2055))
    goflow = await asyncio.create_subprocess_exec(
        "goflow2", "-listen", f"netflow://:{port}", "-format", "json", "-transport", "file",
        stdout=asyncio.subprocess.PIPE, limit=1 << 20)
    log.info("listening for NetFlow v9 on UDP %d", port)
    ctx, loaded, flushed, buffer = Context(), 0.0, time.monotonic(), []
    counts = {"stored": 0, "dropped": 0}
    while True:
        if time.monotonic() - loaded > CONTEXT_SECONDS:
            try:
                ctx, loaded = await load_context(nb, prometheus), time.monotonic()
            except Exception:   # NetBox or Prometheus away: keep the last context
                log.exception("context read failed")
                loaded = time.monotonic() - CONTEXT_SECONDS + 10
        try:
            line = await asyncio.wait_for(goflow.stdout.readline(), FLUSH_SECONDS)
        except asyncio.TimeoutError:
            line = None
        if line == b"":
            raise SystemExit(f"goflow2 stopped (exit {await goflow.wait()})")
        if line:
            try:
                found = row(json.loads(line), ctx)
            except (ValueError, TypeError):
                found = None
            if found is None:
                counts["dropped"] += 1
            else:
                buffer.append(found)
        if buffer and (time.monotonic() - flushed >= FLUSH_SECONDS or len(buffer) >= 5000):
            try:
                await clickhouse.insert(buffer)
                counts["stored"] += len(buffer)
                buffer = []
            except Exception:   # kept and sent with the next batch
                log.exception("insert failed (%d rows kept)", len(buffer))
                buffer = buffer[-50000:]
            flushed = time.monotonic()
            log.info("flows stored %(stored)d, dropped %(dropped)d (unknown switch or not IPv4)", counts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per NetBox call drowned the counts
    asyncio.run(run())
