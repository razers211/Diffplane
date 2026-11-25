"""Response models of the main API routes: what the GUI, the CLI and scripts may rely on.

The answers built here are strict (`extra="forbid"`): a field a route adds without its model fails
its tests with a 500 instead of being dropped silently. Values read as JSON from NetBox custom
fields (`Stored`) keep keys the model does not name. A field with a default is a key the answer
may leave out; routes answer with `response_model_exclude_unset`, so an answer is the dict the
route built, unchanged.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stored(BaseModel):
    model_config = ConfigDict(extra="allow")


class JobId(Answer):
    id: int


class Health(Answer):
    status: Literal["ok", "error"]
    netbox: dict[str, Any]


class Me(Answer):
    username: str
    role: Literal["viewer", "operator", "approver", "admin"]
    fabrics: dict[str, bool] | None   # None: every fabric; else site -> may approve deploys there
    source: str                       # local, ldap or radius


class Fabric(Answer):
    site: str
    name: str
    architecture: Literal["symmetric", "centralized"]


class Device(Answer):
    name: str
    site: str | None
    role: str | None
    status: str | None
    primary_ip: str | None
    location: str | None
    rack: str | None
    position: float | None
    face: Literal["front", "rear"] | None
    pinned: bool                      # SSH host keys pinned: nothing connects to it before
    meth_mac: str | None
    peer: str | None                  # M-LAG peer


class RelayServer(Stored):
    address: str
    service: str = None               # one of: behind this service, or in this peered segment (VNI)
    segment: int = None


class Relay(Stored):
    servers: list[RelayServer]
    loopback: int


class SegmentIpv6(Answer):
    gateway: str
    hosts: Literal["slaac", "dhcpv6", "none"]
    servers: list[RelayServer]


class Routing(Answer):
    gateway: str                      # the anycast gateway, the subnet's first host /length
    peers: list[int]                  # VNIs of explicitly peered segments
    firewall: str | None
    relay: Relay = None               # DHCP relay on
    ipv6: SegmentIpv6 = None          # dual-stack


class Attachment(Answer):
    device: str
    interface: str
    vid: int | None                   # None: untagged


class Segment(Answer):
    fabric: str | None                # None: from before fabrics, delete only
    name: str
    vni: int
    bd_id: int | None
    routing: Routing | None
    attachments: list[Attachment]


class ServiceIpv6(Stored):
    address: str
    next_hop: str
    addresses: dict[str, str] = None  # on an M-LAG pair: each leaf's own address


class Service(Answer):
    name: str
    vrf: str
    rd_vlan: int | None
    device: str | None
    port: str | None
    vlan: int | None
    address: str | None
    next_hop: str | None
    asn: int | None
    kind: Literal["firewall", "isp"]
    devices: list[str | None]
    addresses: dict[str, str] | None
    ipv6: ServiceIpv6 | None


class DiffSnapshot(Answer):
    device: str
    status: Literal["pending", "ok", "error"]
    has_changes: bool | None
    diff: str | None
    error: str | None
    created_at: str
    finished_at: str | None
    job_id: int
    job_kind: str | None              # diff, drift, or deploy (recorded when a deploy was verified)


class DiffJob(Answer):
    id: int
    kind: str                         # diff (someone) or drift (the scheduler)
    requested_by: str | None
    status: Literal["running", "done", "failed"]
    site: str | None
    created_at: str
    finished_at: str | None
    error: str | None
    devices: int
    changed: int
    errors: int
    snapshots: list[DiffSnapshot] = None   # one job asked for: its switches


class DeployStep(Answer):
    device: str
    status: str
    patch: str | None
    error: str | None
    started_at: str | None
    finished_at: str | None


class LogLine(Answer):
    at: str
    device: str | None
    level: str
    message: str


class DeployJob(Answer):
    id: int
    kind: str
    status: str
    site: str | None
    requested_by: str | None
    approved_by: str | None
    approved_at: str | None
    generators: list[str] | None
    created_at: str
    finished_at: str | None
    error: str | None
    window_start: str | None
    window_end: str | None
    checks: dict[str, Any] | None     # pre-deploy checks, as when prepared or last approved
    params: dict[str, Any] | None
    steps: list[DeployStep]
    log: list[LogLine] = None         # one job asked for: its log


class DriftScheduler(Answer):
    interval_seconds: float
    last_started: float | None        # unix seconds
    last_job_id: int | None
    last_skip: str | None
    next_run: float | None


class DriftDevice(Answer):
    device: str
    site: str | None
    state: Literal["in_sync", "out_of_sync", "error", "unknown"]
    checked_at: str | None
    job_id: int | None
    job_kind: str | None
    diff: str | None
    error: str | None


class Drift(Answer):
    scheduler: DriftScheduler | None
    devices: list[DriftDevice]
