"""Segments (VXLAN-EVPN L2VPNs) and their attachment to ports, written to NetBox.

The orchestrator is the only writer. Data model:
  segment     NetBox L2VPN, type vxlan-evpn, identifier = VNI, custom fields bd_id and
              fabric (site); name stored as "<site>:<name>". Unique per fabric.
  attachment  interface custom field `segments`: [{"vni": N, "vid": X or null}]
  generators  read {vni: {bd_id, name}} from the fabric's config context
              "segments-<site>", republished here after every segment change.
"""

import asyncio
import ipaddress
import re
from dataclasses import dataclass

from generators.fabric import VPN_NAME_MAX, segment_vpn
from orchestrator import documents
from orchestrator.netbox import NetBoxClient
from generators import naming

MAX_ID = 16777215  # 24-bit VNI; also the bridge-domain ID range used here
# derives the L3 VNI as L2 VNI + L3_VNI_OFFSET, which must still fit.
L3_VNI_OFFSET = 1_000_000
MAX_L2_VNI = MAX_ID - L3_VNI_OFFSET
LEAF_ROLES = ("leaf", "border-leaf")
LEGACY_CONTEXT = "segments"   # before segments were per fabric
PEER_LINK_TAG = "peer-link"
MLAG_TAG = "m-lag"   # an Eth-Trunk bound to its pair's M-LAG
UNNUMBERED_TAG = "unnumbered"   # a fabric link with no address of its own
SLUG = re.compile(r"[^a-z0-9]+")
# A segment's name names its VPN instance <name>-<bd_id> on the switches.
SEGMENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


class SegmentError(Exception):
    pass


@dataclass(frozen=True)
class Attachment:
    device: str
    interface: str
    vid: int | None


def _slug(name: str) -> str:
    return SLUG.sub("-", name.lower()).strip("-")


def _cf(obj: dict, name: str):
    return (obj.get("custom_fields") or {}).get(name)


def _ref_id(value) -> int | None:
    return value.get("id") if isinstance(value, dict) else value


def display_name(l2vpn: dict) -> str:
    """NetBox L2VPN names are global, so they are stored as "<site>:<name>"."""
    return l2vpn["name"].split(":", 1)[1] if ":" in l2vpn["name"] else l2vpn["name"]


def context_name(fabric: str) -> str:
    return f"segments-{fabric}"


def name_error(name: str, bd_id: int) -> str | None:
    """Why this segment name cannot name its VPN instance <name>-<bd_id>, or None."""
    if not SEGMENT_NAME.fullmatch(name):
        return (f"{name!r}: a segment name starts with a letter and has only letters, digits, '-' and '_' "
                "(it names the segment's VPN instance <name>-<bridge-domain ID>)")
    vpn = segment_vpn(name, bd_id)
    if len(vpn) > VPN_NAME_MAX:
        return f"{name!r} is too long: its VPN instance {vpn} has {len(vpn)} characters, at most {VPN_NAME_MAX}"
    return None


def _port_key(name: str) -> tuple:
    """Natural order: 10GE1/0/2 before 10GE1/0/10."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name))


def subif_number(bd_id: int, vid: int | None) -> int:
    """Numbering: tagged -> <port>.<bd_id>, untagged -> <port>.1."""
    return bd_id if vid is not None else 1


class Segments:
    """Segments of one fabric at a time: every call names the fabric (site slug)."""

    def __init__(self, netbox: NetBoxClient):
        self.nb = netbox

    # -- segments -----------------------------------------------------------------------

    async def _site(self, fabric: str) -> dict:
        site = await self.nb.find_one("dcim/sites", slug=fabric)
        if site is None:
            raise SegmentError(f"no fabric {fabric!r}")
        return site

    async def _all_l2vpns(self) -> list[dict]:
        return await self.nb.find("vpn/l2vpns", type="vxlan-evpn")

    async def _l2vpns(self, site: dict) -> list[dict]:
        return [l for l in await self._all_l2vpns() if _ref_id(_cf(l, "fabric")) == site["id"]]

    async def all(self, fabric: str) -> list[dict]:
        site = await self._site(fabric)
        await self._adopt_legacy()   # may write: before the reads below
        attachments, everything = await asyncio.gather(self._attachments(site), self._all_l2vpns())
        mine = [l for l in everything if _ref_id(_cf(l, "fabric")) == site["id"]]
        routings = await asyncio.gather(*(self._routing(l) for l in mine))
        segments = [
            {
                "fabric": fabric,
                "name": display_name(l),
                "vni": l["identifier"],
                "bd_id": _cf(l, "bd_id"),
                "routing": routing,
                "attachments": [
                    {"device": a.device, "interface": a.interface, "vid": a.vid}
                    for a in attachments.get(l["identifier"], [])
                ],
            }
            for l, routing in zip(mine, routings)
        ]
        # Segments from before fabrics, with no attachment to place them: delete only.
        segments += [
            {"fabric": None, "name": display_name(l), "vni": l["identifier"], "bd_id": _cf(l, "bd_id"),
             "routing": None, "attachments": []}
            for l in everything if _cf(l, "fabric") is None
        ]
        return sorted(segments, key=lambda s: (s["fabric"] is None, s["vni"]))

    async def next_bd_id(self, fabric: str) -> int:
        site = await self._site(fabric)
        used = [_cf(l, "bd_id") for l in await self._l2vpns(site) if _cf(l, "bd_id")]
        return max(used, default=0) + 1

    async def create(self, fabric: str, name: str, vni: int, bd_id: int) -> dict:
        site = await self._site(fabric)
        name = name.strip()
        if not 1 <= vni <= MAX_L2_VNI:
            raise SegmentError(f"VNI must be 1-{MAX_L2_VNI} (its L3 VNI is VNI + {L3_VNI_OFFSET:,})")
        context = await documents.find(self.nb, f"fabric-{fabric}")
        asn = (((context or {}).get("data") or {}).get("fabric") or {}).get("asn")
        overrides = await documents.find(self.nb, f"overrides-{fabric}")
        own_rt = ((((overrides or {}).get("data") or {}).get("overrides") or {}).get("segments") or {}).get(str(vni), {})
        if asn is not None and asn > 65535 and vni > 65535 and not own_rt.get("l2_rt"):
            # Route targets are <ASN>:<VNI>: a 4-byte ASN leaves 2 bytes for the VNI;
            # an imported segment with its own route target (imported fabrics) is not bound by it.
            raise SegmentError(f"{fabric} has the 4-byte ASN {asn}: its VNIs are 1-65535 (route target <ASN>:<VNI>)")
        if not 1 <= bd_id <= MAX_ID:
            raise SegmentError(f"bridge-domain ID must be 1-{MAX_ID}")
        if (error := name_error(name, bd_id)) is not None:
            raise SegmentError(error)
        services = [v for v in await self.nb.find("ipam/vrfs")
                    if _ref_id(_cf(v, "fabric")) == site["id"] and _cf(v, "service_device")]
        # A shared VPN instance keeps the RD VLAN of the service that made it.
        if bd_id in {_cf(v, "service_vlan") for v in services} | {(_cf(v, "service_vrf") or {}).get("rd_vlan")
                                                                  for v in services}:
            raise SegmentError(f"bridge-domain ID {bd_id} equals a firewall service VLAN of {fabric}; "
                               "their route distinguishers would clash")
        for l in await self._l2vpns(site):
            if l["identifier"] == vni:
                raise SegmentError(f"VNI {vni} is already used by segment {display_name(l)!r} in {fabric}")
            if _cf(l, "bd_id") == bd_id:
                raise SegmentError(f"bridge-domain ID {bd_id} is already used by segment {display_name(l)!r} in {fabric}")
            if display_name(l) == name:
                raise SegmentError(f"{fabric} already has a segment named {name!r}")
        await self.nb.create(
            "vpn/l2vpns",
            {"name": f"{fabric}:{name}", "slug": f"{fabric}-{_slug(name)}", "type": "vxlan-evpn",
             "identifier": vni, "custom_fields": {"bd_id": bd_id, "fabric": site["id"]}},
        )
        await self.publish(fabric)
        return {"fabric": fabric, "name": name, "vni": vni, "bd_id": bd_id}

    async def rename(self, fabric: str, vni: int, name: str) -> dict:
        """Rename a segment. A routed segment's VPN instance is renamed
        with it: removed and recreated at the next deploy, and routing intent follows."""
        from orchestrator import dci

        if (link := await dci.stretched(self.nb, fabric, vni)) is not None:   # same name on both sides
            raise SegmentError(f"segment {vni} is stretched over DC link {link} under its name: stop stretching it first")
        site = await self._site(fabric)
        name = name.strip()
        segments = await self._l2vpns(site)
        l2vpn = next((l for l in segments if l["identifier"] == vni), None)
        if l2vpn is None:
            raise SegmentError(f"{fabric} has no segment with VNI {vni}")
        old, bd_id = display_name(l2vpn), _cf(l2vpn, "bd_id")
        result = {"fabric": fabric, "vni": vni, "bd_id": bd_id, "name": name, "routed": False, "intent_updated": []}
        if name == old:
            return result
        if (error := name_error(name, bd_id)) is not None:
            raise SegmentError(error)
        if any(display_name(l) == name for l in segments):
            raise SegmentError(f"{fabric} already has a segment named {name!r}")
        if await self._routing(l2vpn) is not None:
            from orchestrator.routing import Routing

            await Routing(self.nb)._no_hosted(fabric, old, bd_id, "renaming the segment")
        await self.nb.update("vpn/l2vpns", l2vpn["id"], {"name": f"{fabric}:{name}", "slug": f"{fabric}-{_slug(name)}"})
        if await self._routing(l2vpn) is not None:
            from orchestrator.routing_intent import RoutingStore

            result.update(routed=True, vpn_instance=segment_vpn(name, bd_id), replaces=segment_vpn(old, bd_id))
            result["intent_updated"] = await RoutingStore(self.nb).rename_vpn(
                fabric, segment_vpn(old, bd_id), segment_vpn(name, bd_id))
        await self.publish(fabric)
        return result

    async def delete(self, fabric: str, vni: int) -> None:
        if fabric == "unassigned":
            l2vpn = next((l for l in await self._all_l2vpns()
                          if _cf(l, "fabric") is None and l["identifier"] == vni), None)
            if l2vpn is None:
                raise SegmentError(f"no unassigned segment with VNI {vni}")
            await self.nb.delete("vpn/l2vpns", l2vpn["id"])
            return
        from orchestrator import dci

        if (link := await dci.stretched(self.nb, fabric, vni)) is not None:
            raise SegmentError(f"segment {vni} is stretched over DC link {link}: stop stretching it first")
        site = await self._site(fabric)
        l2vpn = await self._l2vpn(site, vni)
        if (await self._attachments(site)).get(vni):
            raise SegmentError(f"segment {vni} is still attached to ports; detach it first")
        if _cf(l2vpn, "gateway_prefix") is not None:
            raise SegmentError(f"segment {vni} is routed; make it not routed first (frees its gateway subnet)")
        await self.nb.delete("vpn/l2vpns", l2vpn["id"])
        await self.publish(fabric)

    async def publish(self, fabric: str) -> None:
        """Write {vni: {bd_id, name}} to the fabric's config context "segments-<site>"."""
        site = await self._site(fabric)
        from orchestrator.routing import Routing  # routing builds on this module

        # each relaying segment's loopback on every leaf with its gateway, made before the
        # context so it lists their addresses (a firewall's leaf routes them back).
        from orchestrator import dhcp_relay

        l2vpns = await self._l2vpns(site)
        relay_loopbacks, relay_loopbacks6 = await dhcp_relay.sync(self.nb, fabric, l2vpns, lambda: self._attachments(site))
        segments = {}
        for l in l2vpns:
            entry = {"bd_id": _cf(l, "bd_id"), "name": display_name(l)}
            if (routing := await self._routing(l)) is not None:
                if "relay" in routing:
                    routing["relay"] = {**routing["relay"], "loopbacks": relay_loopbacks.get(l["identifier"], {}),
                                        **({"loopbacks6": relay_loopbacks6[l["identifier"]]}
                                           if l["identifier"] in relay_loopbacks6 else {})}
                entry["routing"] = routing
            segments[str(l["identifier"])] = entry
        services = {}
        for s in await Routing(self.nb).services(fabric):
            name = s.pop("name")
            if s["vrf"] == name:   # its own instance, RD from its VLAN: the generators' default
                del s["vrf"], s["rd_vlan"]
            services[name] = s
        # a newly routed segment's EPG gets its ID before the generators read it.
        from orchestrator import microseg

        await microseg.sync(self.nb, fabric, segments)
        data = {"segments": segments, "services": services}
        name = context_name(fabric)
        existing = await documents.find(self.nb, name)
        if existing is None:
            await documents.create(self.nb, name, data, [site["id"]])
        elif existing.get("data") != data:
            await documents.save(self.nb, existing, data)
        # The pre-fabric global context would apply to every fabric's devices.
        legacy = await documents.find(self.nb, LEGACY_CONTEXT)
        if legacy is not None:
            await documents.remove(self.nb, legacy)

    async def _adopt_legacy(self) -> None:
        """Segments from before fabrics: give them the fabric of their attachments."""
        legacy = [l for l in await self._all_l2vpns() if _cf(l, "fabric") is None]
        if not legacy:
            return
        by_site: dict[int, set[int]] = {}
        sites = {}
        for site in await self.nb.find("dcim/sites"):
            sites[site["id"]] = site
            for vni in (await self._attachments(site)):
                by_site.setdefault(vni, set()).add(site["id"])
        for l in legacy:
            owners = by_site.get(l["identifier"], set())
            if len(owners) == 1:
                (site_id,) = owners
                slug = sites[site_id]["slug"]
                await self.nb.update("vpn/l2vpns", l["id"], {
                    "name": f"{slug}:{display_name(l)}", "custom_fields": {"fabric": site_id},
                })
                await self.publish(slug)

    async def _routing(self, l2vpn: dict) -> dict | None:
        """{gateway, peers, firewall} of a routed segment; the gateway is the subnet's first host."""
        prefix_id = _ref_id(_cf(l2vpn, "gateway_prefix"))
        if prefix_id is None:
            return None
        found = await self.nb.find("ipam/prefixes", id=prefix_id)
        if not found:
            return None
        network = ipaddress.ip_network(found[0]["prefix"])
        return {
            "gateway": f"{next(network.hosts())}/{network.prefixlen}",
            "peers": sorted(_cf(l2vpn, "peers") or []),
            "firewall": _cf(l2vpn, "firewall"),
            **({"relay": _cf(l2vpn, "dhcp_relay")} if _cf(l2vpn, "dhcp_relay") else {}),
            **(await self._ipv6(l2vpn)),
        }

    async def _ipv6(self, l2vpn: dict) -> dict:
        """{"ipv6": {gateway ::1/64, hosts, servers}} of a dual-stack segment, else {}."""
        prefix_id = _ref_id(_cf(l2vpn, "gateway_prefix6"))
        if prefix_id is None:
            return {}
        found = await self.nb.find("ipam/prefixes", id=prefix_id)
        if not found:
            return {}
        network = ipaddress.ip_network(found[0]["prefix"])
        setting = _cf(l2vpn, "ipv6") or {}
        return {"ipv6": {"gateway": f"{network.network_address + 1}/{network.prefixlen}",
                         "hosts": setting.get("hosts") or "slaac", "servers": setting.get("servers") or []}}

    async def _service_ports(self, site_id: int | None) -> set[tuple[int, str]]:
        """(device id, port) of the fabric's services; a service on an M-LAG pair (it has
        per-leaf addresses) holds its server trunk on both leaves."""
        vrfs = [v for v in await self.nb.find("ipam/vrfs")
                if _ref_id(_cf(v, "fabric")) == site_id and _cf(v, "service_device")]
        peers = {}
        if any(_cf(v, "service_addresses") for v in vrfs):
            peers = {d["id"]: _ref_id(_cf(d, "mlag_peer")) for d in await self.nb.find("dcim/devices")}
        ports = set()
        for v in vrfs:
            device = _ref_id(_cf(v, "service_device"))
            ports.add((device, _cf(v, "service_port")))
            if _cf(v, "service_addresses") and peers.get(device) is not None:
                ports.add((peers[device], _cf(v, "service_port")))
        return ports

    async def _l2vpn(self, site: dict, vni: int) -> dict:
        found = [l for l in await self._l2vpns(site) if l["identifier"] == vni]
        if not found:
            raise SegmentError(f"no segment with VNI {vni} in {site['slug']}")
        return found[0]

    # -- attachments ----------------------------------------------------------------------

    async def _leaves(self, site: dict) -> list[dict]:
        """Devices whose ports may carry segments: leaves, and spines in an M-LAG pair (only a
        centralized fabric has those: its gateway pair, or spines acting as border leaves)."""
        devices = await self.nb.find("dcim/devices", site_id=site["id"])
        return [d for d in devices if (d.get("role") or {}).get("slug") in LEAF_ROLES or _cf(d, "mlag_peer")]

    async def _attachments(self, site: dict) -> dict[int, list[Attachment]]:
        result: dict[int, list[Attachment]] = {}
        leaves = await self._leaves(site)
        found = await asyncio.gather(*(self.nb.find("dcim/interfaces", device_id=d["id"]) for d in leaves))
        for device, interfaces in zip(leaves, found):
            for iface in interfaces:
                for entry in _cf(iface, "segments") or []:
                    result.setdefault(entry["vni"], []).append(
                        Attachment(device["name"], iface["name"], entry.get("vid"))
                    )
        for items in result.values():
            items.sort(key=lambda a: (a.device, a.interface))
        return result

    @staticmethod
    def _refusal(device_name: str, iface: dict, routed: bool) -> str | None:
        """Why this port cannot carry segments, or None if it can."""
        interface = iface["name"]
        kind = (iface.get("type") or {}).get("value")
        if kind == "virtual" or naming.is_management(interface) or naming.is_virtual(interface) or "." in interface:
            return f"{device_name} {interface}: only physical ports and server trunks carry segments"
        if iface.get("lag"):
            return f"{device_name} {interface} is a trunk member; attach to its trunk instead"
        if any(t.get("slug") == PEER_LINK_TAG for t in iface.get("tags") or [] if isinstance(t, dict)):
            return f"{device_name} {interface} is the M-LAG peer-link"
        if routed or any(t.get("slug") == UNNUMBERED_TAG for t in iface.get("tags") or [] if isinstance(t, dict)):
            return f"{device_name} {interface} is a routed fabric link"
        return None

    async def _leaf(self, device_name: str, site: dict | None = None) -> dict:
        device = await self.nb.find_one("dcim/devices", name=device_name)
        if device is None:
            raise SegmentError(f"no device {device_name!r}")
        if site is not None and _ref_id(device.get("site")) != site["id"]:
            raise SegmentError(f"{device_name} is not in fabric {site['slug']}")
        if (device.get("role") or {}).get("slug") not in LEAF_ROLES:
            # A centralized fabric's gateway pair may be spines: their ports carry services.
            # Any M-LAG spine pair there acts as a border leaf too.
            from orchestrator.routing_intent import RoutingStore

            gateways = await RoutingStore(self.nb).gateways((device.get("site") or {}).get("slug"))
            if gateways is None or (device_name not in gateways and not _cf(device, "mlag_peer")):
                raise SegmentError(f"{device_name} is not a leaf; segments attach to leaf ports")
        return device

    async def _port(self, device_name: str, interface: str, site: dict | None = None) -> tuple[dict, dict]:
        device = await self._leaf(device_name, site)
        iface = await self.nb.find_one("dcim/interfaces", device_id=device["id"], name=interface)
        if iface is None:
            raise SegmentError(f"{device_name} has no interface {interface!r}")
        routed = bool(await self.nb.find("ipam/ip-addresses", interface_id=iface["id"]))
        if (reason := self._refusal(device_name, iface, routed)) is not None:
            raise SegmentError(reason)
        return device, iface

    async def ports(self, device_name: str, with_services: bool = False) -> list[dict]:
        """Ports of a leaf that may carry segments, with what they carry now. `with_services`: also
        the ports services sit on (a port may carry several services)."""
        # Three NetBox requests in all, not three per port: a leaf has 100+ interfaces.
        device = await self._leaf(device_name)
        interfaces, addresses, services, vlan_trunks = await asyncio.gather(
            self.nb.find("dcim/interfaces", device_id=device["id"]),
            self.nb.find("ipam/ip-addresses", device_id=device["id"]),
            self._service_ports(_ref_id(device.get("site"))),
            self._vlan_trunks((device.get("site") or {}).get("slug")))
        routed = {ip.get("assigned_object_id") for ip in addresses}
        service = {port for dev, port in services if dev == device["id"]}
        result = []
        for iface in interfaces:
            if (self._refusal(device_name, iface, iface["id"] in routed) is not None
                    or (iface["name"] in service and not with_services)
                    or iface["name"] in vlan_trunks.get(device_name, ())):
                continue
            result.append({
                "name": iface["name"],
                "trunk": (iface.get("type") or {}).get("value") == "lag",
                "mlag": any(t.get("slug") == MLAG_TAG for t in iface.get("tags") or [] if isinstance(t, dict)),
                "segments": _cf(iface, "segments") or [],
            })
        return sorted(result, key=lambda p: (not p["trunk"], _port_key(p["name"])))

    async def _mirror(self, device: dict, iface: dict) -> tuple[dict, dict] | None:
        """The same server trunk on the M-LAG peer, if this is an M-LAG trunk of a pair."""
        if (iface.get("type") or {}).get("value") != "lag":
            return None
        if not any(t.get("slug") == MLAG_TAG for t in iface.get("tags") or [] if isinstance(t, dict)):
            return None   # a single trunk: this device's alone
        peer = _cf(device, "mlag_peer")
        if not peer:
            return None
        peer_name = peer.get("name") if isinstance(peer, dict) else None
        if peer_name is None:
            peer_device = next((d for d in await self.nb.find("dcim/devices") if d["id"] == peer.get("id")), None)
            peer_name = peer_device["name"] if peer_device else None
        if peer_name is None:
            return None
        try:
            return await self._port(peer_name, iface["name"], {"id": _ref_id(device.get("site")),
                                                               "slug": "the same fabric"})
        except SegmentError as exc:
            raise SegmentError(f"M-LAG peer: {exc}") from exc

    async def _vlan_trunks(self, fabric: str) -> dict[str, set[str]]:
        """Ports each switch keeps as VLAN trunks (an import's overrides). VRP refuses an
        L2 sub-interface for a VLAN its main port already passes ("the VLAN has been configured on
        interface Eth-Trunk1", a lab test), so such a port carries no segments."""
        context = await documents.find(self.nb, f"overrides-{fabric}")
        devices = ((((context or {}).get("data") or {}).get("overrides") or {}).get("devices") or {})
        return {name: set(values.get("vlan_trunks") or {}) for name, values in devices.items()}

    async def _border(self, device: dict) -> bool:
        """A border leaf of a symmetric fabric (routing only)."""
        if (device.get("role") or {}).get("slug") != "border-leaf":
            return False
        from orchestrator.routing_intent import RoutingStore

        return await RoutingStore(self.nb).gateways((device.get("site") or {}).get("slug")) is None

    async def _write(self, iface: dict, entries: list[dict]) -> None:
        await self.nb.update("dcim/interfaces", iface["id"], {"custom_fields": {"segments": entries or None}})

    @staticmethod
    def _check(device: str, interface: str, entries: list[dict], bd: dict[int, int]) -> None:
        untagged = [e for e in entries if e.get("vid") is None]
        if len(untagged) > 1:
            raise SegmentError(f"{device} {interface}: only one untagged segment per port")
        vids = [e["vid"] for e in entries if e.get("vid") is not None]
        if len(vids) != len(set(vids)):
            raise SegmentError(f"{device} {interface}: a VLAN ID can carry only one segment per port")
        numbers = [subif_number(bd[e["vni"]], e.get("vid")) for e in entries]
        if len(numbers) != len(set(numbers)):
            raise SegmentError(f"{device} {interface}: sub-interface numbers collide (untagged uses .1)")

    async def _change(self, fabric: str, device_name: str, interface: str, vni: int, change) -> list[str]:
        """Apply `change(entries) -> entries` on the port and, for an M-LAG trunk, its mirror."""
        site = await self._site(fabric)
        segment = await self._l2vpn(site, vni)
        segment = {**segment, "name": display_name(segment)}
        bd = {l["identifier"]: _cf(l, "bd_id") for l in await self._l2vpns(site)}
        device, iface = await self._port(device_name, interface, site)
        if (device["id"], iface["name"]) in await self._service_ports(site["id"]):
            raise SegmentError(f"{device_name} {interface} is a firewall service port")
        targets = [(device, iface)]
        if (mirror := await self._mirror(device, iface)) is not None:
            targets.append(mirror)
        planned = []
        for dev, port in targets:
            entries = change([dict(e) for e in _cf(port, "segments") or []], segment)
            self._check(dev["name"], port["name"], entries, bd)
            planned.append((dev, port, entries))
        vlan_trunks = await self._vlan_trunks(site["slug"])
        for dev, port, entries in planned:
            added = any(e["vni"] == vni for e in entries) and all(e["vni"] != vni for e in _cf(port, "segments") or [])
            if added and port["name"] in vlan_trunks.get(dev["name"], ()):
                raise SegmentError(f"{dev['name']} {port['name']} is a VLAN trunk (port trunk allow-pass vlan): "
                                   "the switch refuses a segment's sub-interface on it")
            if added and await self._border(dev):
                raise SegmentError(f"{dev['name']} is a border leaf: it only routes (services and EVPN), "
                                   "its ports carry no segments")
        removed = {(dev["name"], port["name"]) for dev, port, entries in planned
                   if any(e["vni"] == vni for e in _cf(port, "segments") or []) and all(e["vni"] != vni for e in entries)}
        if removed:
            from orchestrator.routing_intent import RoutingStore

            def detached(services, segments):
                return services, [{**s, "attachments": [a for a in s["attachments"]
                                                        if (a["device"], a["interface"]) not in removed]}
                                  if s["vni"] == vni else s for s in segments]

            await RoutingStore(self.nb).guard(fabric, f"detaching segment {segment['name']} from "
                                              f"{', '.join(' '.join(r) for r in sorted(removed))}", detached)
        for _, port, entries in planned:
            await self._write(port, entries)
        # a first or last port moves a relaying segment's gateway, and with it its relay loopback.
        if any(_cf(l, "dhcp_relay") for l in await self._l2vpns(await self._site(fabric))):
            await self.publish(fabric)
        else:
            from orchestrator import dhcp_relay

            await dhcp_relay.sync(self.nb, fabric, [], lambda: None)   # removes loopbacks left over, if any
        return [f"{dev['name']} {port['name']}" for dev, port, _ in planned]

    async def attach(self, fabric: str, device: str, interface: str, vni: int, vid: int | None) -> list[str]:
        if vid is not None and not 1 <= vid <= 4094:
            raise SegmentError("VLAN ID must be 1-4094, or empty for untagged")

        def add(entries, segment):
            if any(e["vni"] == vni for e in entries):
                raise SegmentError(f"{device} {interface} already carries segment {segment['name']!r}")
            return sorted(entries + [{"vni": vni, "vid": vid}], key=lambda e: e["vni"])

        return await self._change(fabric, device, interface, vni, add)

    async def set_vid(self, fabric: str, device: str, interface: str, vni: int, vid: int | None) -> list[str]:
        if vid is not None and not 1 <= vid <= 4094:
            raise SegmentError("VLAN ID must be 1-4094, or empty for untagged")

        def update(entries, segment):
            if not any(e["vni"] == vni for e in entries):
                raise SegmentError(f"{device} {interface} does not carry segment {segment['name']!r}")
            return [{**e, "vid": vid} if e["vni"] == vni else e for e in entries]

        return await self._change(fabric, device, interface, vni, update)

    async def detach(self, fabric: str, device: str, interface: str, vni: int) -> list[str]:
        def remove(entries, segment):
            if not any(e["vni"] == vni for e in entries):
                raise SegmentError(f"{device} {interface} does not carry segment {segment['name']!r}")
            return [e for e in entries if e["vni"] != vni]

        return await self._change(fabric, device, interface, vni, remove)
