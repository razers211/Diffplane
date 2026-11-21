"""Intent snapshots: the intent the platform writes, as it was when
a deploy of the fabric was verified, so a switch or the fabric can be rolled back to it through the
normal pipeline (step 3).

A snapshot holds what operators change from the GUI, never what is derived or structural:
segments (VNI, bridge domain, gateway subnet, peers, firewall, attachments), services, the routing
document, server Eth-Trunks, and the fabric settings the GUI owns (NTP, SNMP, gateway MAC).
Devices, cabling and fabric addressing are not in it (database backups cover those).
Kept like config versions: 90 days, always the fabric's last 20.
"""

import asyncio
import hashlib
import json
from datetime import timedelta

from sqlalchemy import delete, select

from orchestrator import documents
from orchestrator.db import IntentSnapshot, now

KEEP_DAYS = 90
KEEP_LAST = 20
FABRIC_KEYS = ("ntp_servers", "ntp_vpn_instance", "snmp", "gateway_mac", "netstream", "syslog", "microsegmentation", "qos", "port_profiles", "protection")   # seed.loader.GUI_KEYS


async def capture_intent(nb, fabric: str) -> dict:
    """The fabric's current intent, in the snapshot's form."""
    from orchestrator.routing import Routing
    from orchestrator.routing_intent import RoutingStore
    from orchestrator.segments import Segments

    segments = Segments(nb)
    await segments._adopt_legacy()   # may write: before the reads below
    every, services, routing, interfaces, context = await asyncio.gather(
        segments.all(fabric), Routing(nb).services(fabric), RoutingStore(nb).load(fabric),
        nb.find("dcim/interfaces", site=fabric), documents.find(nb, f"fabric-{fabric}"))
    settings = ((context or {}).get("data") or {}).get("fabric") or {}
    return {
        "version": 1,
        "segments": [{k: s[k] for k in ("name", "vni", "bd_id", "routing", "attachments")}
                     for s in every if s["fabric"] == fabric],
        "services": [{k: v for k, v in s.items() if k != "devices"} for s in services],
        "routing": routing.model_dump(mode="json", exclude_defaults=True),
        "trunks": _trunks(interfaces),
        "ports": _ports(interfaces),
        "fabric": {k: settings[k] for k in FABRIC_KEYS if k in settings},
        "dci": await _dci_links(nb, fabric),   # the fabric's DC links
        "overrides": (((await documents.find(nb, f"overrides-{fabric}")) or {}).get("data")
                      or {}).get("overrides"),
        # The intent documents' versions this point holds (orchestrator/intent_store.py); read only
        # to show them: restoring uses the values above.
        "documents": await nb.intent.versions(document_names(fabric)) if getattr(nb, "intent", None) else {},
    }


def document_names(fabric: str) -> list[str]:
    """The intent documents a rollback point of the fabric covers."""
    from orchestrator.routing_intent import context_name

    return [f"fabric-{fabric}", context_name(fabric), f"overrides-{fabric}", f"segments-{fabric}", "dci"]


async def _dci_links(nb, fabric: str) -> list[dict]:
    from orchestrator import dci

    return [l for l in (await dci.document(nb))["links"] if any(s["fabric"] == fabric for s in l["sides"])]


def _ports(interfaces: list[dict]) -> dict[str, dict[str, dict]]:
    """Port settings that differ from a port's defaults, per device: name, off, frame size, speed."""
    from orchestrator.port_settings import DEFAULT, PHYSICAL, settings_of

    found: dict[str, dict[str, dict]] = {}
    for iface in interfaces:
        values = settings_of(iface)
        if (PHYSICAL.fullmatch(iface["name"]) or iface["name"].startswith("Eth-Trunk")) \
                and values != DEFAULT:
            found.setdefault(iface["device"]["name"], {})[iface["name"]] = values
    return {device: dict(sorted(ports.items())) for device, ports in sorted(found.items())}


def _trunks(interfaces: list[dict]) -> dict[str, list[dict]]:
    """Server Eth-Trunks per device (peer-links are structural: the fabric YAML's)."""
    from orchestrator.trunks import MLAG_TAG, PEER_LINK_TAG, TRUNK, _kind, _number, _port_key, _tags

    members: dict[int, list[str]] = {}
    for iface in interfaces:
        if iface.get("lag"):
            members.setdefault(iface["lag"]["id"], []).append(iface["name"])
    found: dict[str, list[dict]] = {}
    for iface in interfaces:
        if _kind(iface) != "lag" or not TRUNK.match(iface["name"]) or PEER_LINK_TAG in _tags(iface):
            continue
        found.setdefault(iface["device"]["name"], []).append({
            "number": _number(iface["name"]),
            "members": sorted(members.get(iface["id"], []), key=_port_key),
            "m_lag": MLAG_TAG in _tags(iface),
        })
    return {device: sorted(trunks, key=lambda t: t["number"]) for device, trunks in sorted(found.items())}


def summary(data: dict) -> dict:
    return {"segments": len(data.get("segments", [])), "services": len(data.get("services", [])),
            "routing_targets": len((data.get("routing") or {}).get("devices") or {}),
            "trunks": sum(len(t) for t in (data.get("trunks") or {}).values())}


def snapshot_json(s: IntentSnapshot, with_data: bool = False) -> dict:
    body = {"id": s.id, "site": s.site, "job_id": s.job_id, "taken_at": s.taken_at.isoformat(), **summary(s.data)}
    return {**body, "data": s.data} if with_data else body


class IntentSnapshots:
    def __init__(self, sessions, netbox):
        self.sessions, self.netbox = sessions, netbox

    async def take(self, fabric: str, job_id: int | None) -> dict:
        """Keep the fabric's intent now, tied to the verified deploy `job_id`."""
        data = await capture_intent(self.netbox, fabric)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        async with self.sessions() as session:
            snapshot = IntentSnapshot(site=fabric, job_id=job_id, taken_at=now(), digest=digest, data=data)
            session.add(snapshot)
            await session.commit()
            result = snapshot_json(snapshot)
        await self.prune(fabric)
        return result

    async def prune(self, fabric: str) -> int:
        async with self.sessions() as session:
            ids = (await session.scalars(select(IntentSnapshot.id).where(IntentSnapshot.site == fabric)
                                         .order_by(IntentSnapshot.taken_at.desc(), IntentSnapshot.id.desc()))).all()
            old = ids[KEEP_LAST:]
            if not old:
                return 0
            result = await session.execute(delete(IntentSnapshot).where(
                IntentSnapshot.id.in_(old), IntentSnapshot.taken_at < now() - timedelta(days=KEEP_DAYS)))
            await session.commit()
            return result.rowcount or 0

    async def list(self, fabric: str) -> list[dict]:
        async with self.sessions() as session:
            found = (await session.scalars(select(IntentSnapshot).where(IntentSnapshot.site == fabric)
                                           .order_by(IntentSnapshot.taken_at.desc(), IntentSnapshot.id.desc()))).all()
        return [snapshot_json(s) for s in found]

    async def get(self, fabric: str, snapshot_id: int) -> dict | None:
        async with self.sessions() as session:
            found = await session.get(IntentSnapshot, snapshot_id)
        return snapshot_json(found, with_data=True) if found is not None and found.site == fabric else None
