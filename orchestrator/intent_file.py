"""A fabric's intent as a YAML file: exported to be kept in git, imported back as intent operations.

The file is a rollback point's form (orchestrator/intent_snapshots.py). Importing it plans the
operations that bring today's intent to the file's, as a rollback does (orchestrator/rollback.py),
and runs each through the orchestrator's own checks. The switches change only through the usual
diff, approval and deploy.

A file is untrusted input. It is read with `yaml.safe_load`, aliases refused, its parts type-checked,
and it may change only what an operator may change through those checked operations: segments,
services, server trunks, routing intent, NTP, SNMP location and contact, the gateway MAC,
NetStream and syslog. Everything else in it (port settings, microsegmentation, QoS, protection,
SNMPv3 readers, DC links, what an import kept as found) is set from the GUI by the people allowed
to: the import keeps the current values and says which parts of the file it left out.
"""

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from orchestrator import answers, intent_snapshots, rollback
from orchestrator.redact import redact
from orchestrator.routing_intent import RoutingDocument
from orchestrator.segments import SegmentError

KIND = "diffplane-intent"
VERSION = 1
MAX_BYTES = 2_000_000
# Plan operations an intent file may run: each goes through a checked API function.
IMPORTED = {"routing_set", "routing_all", "detach", "set_vid", "attach", "route", "unroute", "delete_service", "service_asn",
            "create_service", "service_ipv6", "delete_segment", "rename", "create_segment", "delete_trunk",
            "create_trunk", "ntp", "snmp", "relay", "ipv6", "netstream", "syslog", "gateway_mac"}
KEPT = {"ports": "port settings", "dci": "DC links", "overrides": "what the import kept as found"}
KEPT_FABRIC = {"microsegmentation": "microsegmentation", "qos": "QoS", "port_profiles": "port settings managed",
               "protection": "port protection"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Segment(Strict):
    name: str
    vni: int
    bd_id: int | None
    routing: answers.Routing | None = None
    attachments: list[answers.Attachment] = []


class Service(Strict):
    name: str
    vrf: str | None = None
    rd_vlan: int | None = None
    device: str | None
    port: str | None
    vlan: int | None
    address: str | None
    next_hop: str | None
    asn: int | None = None
    kind: str = "firewall"
    addresses: dict[str, str] | None = None
    ipv6: answers.ServiceIpv6 | None = None


class Trunk(Strict):
    number: int
    members: list[str]
    m_lag: bool


class Switchable(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    enabled: bool = False
    host: str | None = None
    port: int | None = None


class Snmp(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    location: str = ""
    contact: str = ""


class Fabric(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    ntp_servers: list[str] | None = None
    ntp_vpn_instance: str | None = None
    snmp: Snmp | None = None
    gateway_mac: str | None = None
    netstream: Switchable | None = None
    syslog: Switchable | None = None


class IntentFile(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    kind: str
    version: int
    fabric: str
    segments: list[Segment] = []
    services: list[Service] = []
    routing: RoutingDocument = RoutingDocument()
    trunks: dict[str, list[Trunk]] = {}
    settings: Fabric = Fabric()


class NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def render(fabric: str, intent: dict) -> str:
    """The file: a header, then the intent in its snapshot form (`fabric` renamed `settings`)."""
    body = {"kind": KIND, "version": VERSION, "fabric": fabric,
            **{("settings" if k == "fabric" else k): v for k, v in intent.items() if k not in ("documents", "version")}}
    return redact(yaml.dump(body, Dumper=NoAliases, sort_keys=False, allow_unicode=True))


async def export(nb, fabric: str) -> str:
    return render(fabric, await intent_snapshots.capture_intent(nb, fabric))


def parse(text: str, fabric: str) -> dict:
    """The file as snapshot data, checked; SegmentError says what is wrong."""
    if len(text.encode()) > MAX_BYTES:
        raise SegmentError(f"the file is over {MAX_BYTES // 1_000_000} MB")
    try:
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(text, Loader=yaml.SafeLoader)):
            raise SegmentError("YAML aliases (*name) are not accepted in an intent file")
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SegmentError(f"not YAML: {exc}") from None
    if not isinstance(data, dict) or data.get("kind") != KIND:
        raise SegmentError(f"not an intent file (it starts with kind: {KIND}; diffplane intent export makes one)")
    if data.get("version") != VERSION:
        raise SegmentError(f"intent file version {data.get('version')!r}: this orchestrator reads version {VERSION}")
    if data.get("fabric") != fabric:
        raise SegmentError(f"this file is for fabric {data.get('fabric')!r}, not {fabric!r}")
    try:
        checked = IntentFile.model_validate(data)
    except ValidationError as exc:
        raise SegmentError("intent file: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10])) from None
    out = checked.model_dump(mode="json", exclude_unset=True)   # a key left out stays out (segment routing)...
    out["services"] = [s.model_dump(mode="json") for s in checked.services]   # ...a service's is its default
    out["fabric"] = out.pop("settings", {})
    for key in ("kind", "version"):
        out.pop(key, None)
    return out


def _merge(current: dict, wanted: dict) -> tuple[dict, list[str]]:
    """What the file may change taken from it; the rest kept as it is now. Also the parts of
    the file left out because they differ from today's."""
    merged, ignored = {**current}, []
    for key in ("segments", "services", "trunks"):
        merged[key] = wanted.get(key, [] if key != "trunks" else {})
    merged["routing"] = {**(current.get("routing") or {}),
                         "devices": (wanted.get("routing") or {}).get("devices") or {},
                         "library": (wanted.get("routing") or {}).get("library") or {}}
    for key, words in KEPT.items():
        if key in wanted and wanted[key] != current.get(key):
            ignored.append(words)
    now, file = current.get("fabric") or {}, wanted.get("fabric") or {}
    fab = {**now}
    for key in ("ntp_servers", "ntp_vpn_instance", "gateway_mac"):
        if key in file:
            fab[key] = file[key]
        else:
            fab.pop(key, None)
    if "snmp" in file:
        fab["snmp"] = {**(now.get("snmp") or {}), **{k: file["snmp"][k] for k in ("location", "contact") if k in file["snmp"]}}
        if {k: v for k, v in file["snmp"].items() if k not in ("location", "contact")} != \
                {k: v for k, v in (now.get("snmp") or {}).items() if k not in ("location", "contact", "owned")}:
            ignored.append("SNMPv3 readers and trap targets")
    for key in ("netstream", "syslog"):
        if key in file:
            fab[key] = {**(now.get(key) or {}), **{k: file[key].get(k) for k in ("enabled", "host", "port")}}
    for key, words in KEPT_FABRIC.items():
        if key in file and file[key] != now.get(key):
            ignored.append(words)
    merged["fabric"] = fab
    return merged, ignored


async def preview(nb, fabric: str, text: str) -> dict:
    """The operations that bring today's intent to the file's; nothing is written."""
    wanted = parse(text, fabric)
    scope = await rollback.scope_for(nb, fabric, None)
    current = await intent_snapshots.capture_intent(nb, fabric)
    merged, ignored = _merge(current, wanted)
    result = rollback.plan(current, merged, scope)
    stray = sorted({o["kind"] for o in result["operations"]} - IMPORTED)
    if stray:   # a section _merge should have kept: never run it from a file
        raise SegmentError(f"an intent file cannot change {', '.join(stray)}")
    for o in result["operations"]:
        o["text"] = ("SNMP location and contact as in the file" if o["kind"] == "snmp"   # readers: today's
                     else o["text"].replace("as at the rollback point", "as in the file"))
    return {"fabric": fabric, **result, "ignored": ignored}


async def apply(nb, fabric: str, text: str, expected: int) -> dict:
    planned = await preview(nb, fabric, text)
    if len(planned["operations"]) != expected:
        raise SegmentError(f"intent changed since the preview ({len(planned['operations'])} operations now, "
                           f"{expected} shown): preview again")
    return await rollback.execute(nb, fabric, planned)
