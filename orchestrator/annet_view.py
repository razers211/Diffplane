"""Our generators through Annet's own pipeline, the way `annet gen` and `annet patch` run them,
on devices presented as Annet's NetBox adapter would (from an in-memory NetBox): the tests'
harness, and the import preview's, which runs in its own process
(orchestrator/import_preview.py) because Annet's hardware and rulebook providers are set once
per process.

Partial generators, ACL filtering, the Huawei rulebook's ordering and the vendor formatter all
come from Annet; only the device is a stub instead of NetBox.
"""

import copy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from annet import patching
from annet.adapters.netbox.common.manufacturer import get_breed
from annet.annlib.netdev.views.hardware import HardwareView
from annet.gen import format_config_blocks
from annet.generators import compile_acl_text, run_partial_generators
from annet.types import GeneratorPartialRunArgs

from generators import get_generators
from orchestrator import documents


class StubStorage:
    """The part of Annet's Storage that generators use: other devices by name."""

    def __init__(self, devices: list["StubDevice"] | None = None):
        self.devices = {d.hostname: d for d in devices or []}

    def make_devices(self, names, **_):
        return [self.devices[n] for n in names if n in self.devices]

    def flush_perf(self):
        return None


_STORAGE = StubStorage()


@dataclass
class StubDevice:
    hostname: str
    model: str
    config_context: dict[str, Any] = field(default_factory=dict)
    role_slug: str = "leaf"
    interfaces: list[Any] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    storage: Any = None

    def __post_init__(self) -> None:
        self.fqdn = self.hostname
        self.id = self.hostname
        self.hw = HardwareView(f"Huawei {self.model}", "")
        self.breed = get_breed("Huawei", self.model)
        self.name = self.hostname
        self.storage = self.storage or _STORAGE
        self.role = SimpleNamespace(slug=self.role_slug)
        self.device_type = SimpleNamespace(model=self.model)   # as Annet's NetBox device (copper ports)

    def __hash__(self) -> int:
        return hash(self.hostname)


def render(device: StubDevice) -> str:
    result = run_partial_generators(
        get_generators(device.storage), [], GeneratorPartialRunArgs(device, use_acl=True)
    )
    check_acl_exclusive(device)
    ordered = patching.Orderer.from_hw(device.hw).order_config(result.config_tree())
    # Two spaces: `annet gen`'s default indent, so golden files match its output.
    return normalize(format_config_blocks(ordered, device.hw, "  "))


def check_acl_exclusive(device: StubDevice) -> None:
    """As `annet gen/diff/patch` do: two generators that run by default may not both be
    able to delete the same line (Annet raises AclNotExclusiveError). Generators tagged
    "disable" only run when named with -g, so they are left out, as Annet does."""
    enabled = [g for g in get_generators(device.storage) if "disable" not in g.TAGS]
    result = run_partial_generators(enabled, [], GeneratorPartialRunArgs(device, use_acl=True))
    acl = compile_acl_text(result.acl_text(), device.hw.vendor)
    patching.apply_acl(result.config_tree(), acl, exclusive=True)


def patch_to(running: str, device: StubDevice, indent: str = " ") -> str:
    """The patch `annet patch` would send to a device whose running config is `running`,
    with every enabled generator's ACL (so removals of lines nobody owns are missing).
    With `indent="  "` (`annet patch`'s default) it is printed as `annet patch` prints it."""
    from annet.api import _diff_and_patch, _format_patch_blocks
    from annet.vendors import registry_connector, tabparser

    enabled = [g for g in get_generators(device.storage) if "disable" not in g.TAGS]
    result = run_partial_generators(enabled, [], GeneratorPartialRunArgs(device, use_acl=True))
    acl = compile_acl_text(result.acl_text(), device.hw.vendor)
    split = registry_connector.get().match(device.hw).make_formatter().split
    old = patching.apply_acl(tabparser.parse_to_tree(running, split), acl)
    new = tabparser.parse_to_tree(render(device), split)
    _, tree = _diff_and_patch(device, old, new, acl, None, False)
    return _format_patch_blocks(tree, device.hw, indent)


def normalize(config: str) -> str:
    return config.rstrip("\n") + "\n"


def devices_from_netbox(nb, slug: str) -> list[StubDevice]:
    """A fabric's devices in the fake NetBox, as Annet's NetBox adapter presents them."""
    import asyncio

    fabric = SimpleNamespace(slug=slug)

    async def view() -> list[StubDevice]:
        roles = {r["id"]: r["slug"] for r in await nb.find("dcim/device-roles")}
        tags = {t["id"]: t["slug"] for t in await nb.find("extras/tags")}
        names = {d["id"]: d["name"] for d in await nb.find("dcim/devices")}
        # The fabric's config contexts as NetBox merges them for its devices: fabric settings
        # (seed), segments and services (orchestrator), routing intent (orchestrator).
        contexts = {c["name"]: c["data"] for c in nb.objects.get(documents.ENDPOINT, {}).values()}
        context = {}
        for name in (f"fabric-{fabric.slug}", f"segments-{fabric.slug}", f"routing-{fabric.slug}",
                     f"overrides-{fabric.slug}"):
            context.update(contexts.get(name, {}))
        context.update(contexts.get("dci", {}))   # assigned to every linked fabric
        result = []
        for device in sorted(await nb.find("dcim/devices", site=fabric.slug), key=lambda d: d["name"]):
            interfaces = []
            for iface in sorted(await nb.find("dcim/interfaces", device_id=device["id"]), key=lambda i: i["name"]):
                ips = await nb.find("ipam/ip-addresses", interface_id=iface["id"])
                interfaces.append(
                    SimpleNamespace(
                        name=iface["name"],
                        type=SimpleNamespace(value=iface["type"]["value"]),
                        lag=SimpleNamespace(**iface["lag"]) if iface.get("lag") else None,
                        tags=[SimpleNamespace(slug=tags[t["id"]]) for t in iface.get("tags") or []],
                        custom_fields=iface.get("custom_fields") or {},
                        description=iface.get("description") or "",   # port settings
                        enabled=iface.get("enabled", True) is not False,
                        mtu=iface.get("mtu"),
                        speed=iface.get("speed"),
                        ip_addresses=[SimpleNamespace(address=ip["address"], role=ip.get("role")) for ip in ips],
                        connected_endpoints=[
                            SimpleNamespace(name=p["name"], device=SimpleNamespace(name=p["device"]["name"]))
                            for p in iface["link_peers"]
                        ] or None,
                    )
                )
            model = next(t["model"] for t in nb.objects["dcim/device-types"].values()
                         if t["id"] == device["device_type"]["id"])
            peer = (device.get("custom_fields") or {}).get("mlag_peer")
            custom_fields = {"mlag_peer": {"id": peer["id"], "name": names[peer["id"]]}} if peer else {}
            result.append(
                StubDevice(
                    device["name"], model, copy.deepcopy(context),   # tests change one device's
                    role_slug=roles[device["role"]["id"]], interfaces=interfaces,
                    custom_fields=custom_fields, storage=storage,
                )
            )
        return result

    storage = StubStorage()
    devices = asyncio.run(view())
    storage.devices.update({d.hostname: d for d in devices})
    return devices
