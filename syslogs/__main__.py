"""The syslog receiver: `python -m syslogs`.

Listens on UDP `SYSLOG_LISTEN_PORT` (5514 in the container, published as the fabric's syslog port),
keeps the messages of switches the platform knows (by their management address, the datagram's
source), and writes them to ClickHouse every 5 seconds. Unknown senders are dropped. The switches
are read from NetBox again every minute. Nothing is sent to any switch.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from flows.store import ClickHouse
from syslogs import store
from syslogs.parse import parse

log = logging.getLogger("syslogs")
FLUSH_SECONDS, SWITCHES_SECONDS = 5, 60


async def load_switches(nb) -> dict[str, tuple[str, str]]:
    """Management address -> (switch, fabric) of every active switch."""
    from orchestrator.routing_intent import status_of

    found = {}
    for d in await nb.find("dcim/devices"):
        address = ((d.get("primary_ip4") or d.get("primary_ip") or {}).get("address") or "").split("/")[0]
        if address and status_of(d) == "active":
            found[address] = (d["name"], (d.get("site") or {}).get("slug") or "")
    return found


def row(datagram: bytes, sender: str, switches: dict[str, tuple[str, str]], now: datetime) -> dict | None:
    """The stored row of one datagram, or None (unknown sender, not syslog)."""
    known = switches.get(sender)
    message = parse(datagram) if known else None
    if message is None:
        return None
    device, site = known
    return {"time": now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "site": site, "device": device, "address": sender,
            "severity": message["severity"], "facility": message["facility"], "tag": message["tag"],
            "message": message["message"]}


class Receiver(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue[tuple[bytes, str]] = asyncio.Queue(maxsize=100000)

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.queue.put_nowait((data, addr[0]))
        except asyncio.QueueFull:   # ClickHouse far behind: newest messages dropped, never the process
            pass


async def run() -> None:
    from orchestrator.config import Settings
    from orchestrator.netbox import NetBoxClient

    settings = Settings.from_env()
    nb = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
    clickhouse = ClickHouse(os.environ.get("CLICKHOUSE_URL", "http://clickhouse:8123"),
                            os.environ.get("CLICKHOUSE_USER", "flows"), os.environ["CLICKHOUSE_PASSWORD"],
                            cluster=os.environ.get("CLICKHOUSE_CLUSTER") or None)   # replicated on the HA servers
    while True:   # ClickHouse may still be starting
        try:
            await store.create(clickhouse)
            break
        except Exception as exc:
            log.warning("ClickHouse not ready (%s); again in 5 s", exc)
            await asyncio.sleep(5)
    port = int(os.environ.get("SYSLOG_LISTEN_PORT", 5514))
    _, receiver = await asyncio.get_running_loop().create_datagram_endpoint(Receiver, local_addr=("0.0.0.0", port))
    log.info("listening for syslog on UDP %d", port)
    switches, loaded, flushed, buffer = {}, 0.0, time.monotonic(), []
    counts = {"stored": 0, "dropped": 0}
    while True:
        if time.monotonic() - loaded > SWITCHES_SECONDS:
            try:
                switches, loaded = await load_switches(nb), time.monotonic()
            except Exception:   # NetBox away: keep the last list
                log.exception("switch list read failed")
                loaded = time.monotonic() - SWITCHES_SECONDS + 10
        try:
            data, sender = await asyncio.wait_for(receiver.queue.get(), FLUSH_SECONDS)
            found = row(data, sender, switches, datetime.now(timezone.utc))
            if found is None:
                counts["dropped"] += 1
            else:
                buffer.append(found)
        except asyncio.TimeoutError:
            pass
        if buffer and (time.monotonic() - flushed >= FLUSH_SECONDS or len(buffer) >= 5000):
            try:
                await store.insert(clickhouse, buffer)
                counts["stored"] += len(buffer)
                buffer = []
            except Exception:   # kept and sent with the next batch
                log.exception("insert failed (%d rows kept)", len(buffer))
                buffer = buffer[-50000:]
            flushed = time.monotonic()
            log.info("syslog stored %(stored)d, dropped %(dropped)d (unknown sender or not syslog)", counts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run())
