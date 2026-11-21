import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import evpn_peers, fabric_asn
from generators.fabric import FABRIC_ROLES, loopback, vtep_loopback


def document(device: Any) -> dict:
    return (device.config_context or {}).get("dci") or {}


def links(device: Any) -> list[tuple[dict, dict, dict]]:
    """(link, this switch's side, the other side) for every DC link this switch is a border of."""
    found = []
    for link in document(device).get("links") or ():
        a, b = link["sides"]
        for local, remote in ((a, b), (b, a)):
            if device.name in local["pair"]:
                found.append((link, local, remote))
    return found


def uplinks(device: Any) -> list[dict]:
    return [u for _, local, _ in links(device) for u in local["uplinks"].get(device.name, ())]


def uplink_ports(device: Any) -> set[str]:
    """The switch's DCI uplinks: routed ports (no trust, no layer 2)."""
    return {u["port"] for u in uplinks(device)}


def stretched(device: Any) -> dict[int, tuple[int, str]]:
    """{local L2 VNI: (DCI VNI, split group)} of the segments stretched over this switch's links."""
    found = {}
    for link, local, _ in links(device):
        for segment in link.get("segments") or ():
            found[int(segment["vnis"][local["fabric"]])] = (int(segment["dci_vni"]), link["split_group"])
    return found


def _retired(device: Any) -> dict:
    return (document(device).get("retired") or {}).get(device.name) or {}


class Dci(PartialGenerator):
    """EVPN-VXLAN DCI on a DC link's border pair.

    Underlay: each DCI uplink is a routed port with eBGP IPv4 to its neighbour (the other DC's
    border over dark fibre, or a provider PE), advertising only this switch's LoopBack0 and the
    pair's VTEP. Overlay: eBGP EVPN between the borders' LoopBack0s (multihop), the remote borders
    in the link's split group; MAC and MAC/IP routes re-originated both ways (`import reoriginate`
    and `advertise route-reoriginated evpn mac-ip|mac` on the remote borders and on this switch's
    own route reflectors). A stretched segment's bridge domain and mapped VNI are BridgeDomains'.
    VRP adds `peer <ip> enable` under `ipv4-family unicast` for every new peer, so it is
    written for the EVPN peers too. Forms as V300R025 stores them.

    Owns its peers and uplink ports by address and name, also those its links no longer have
    (retired, until a verified deploy), and every re-origination and split-group line by kind.
    """

    TAGS = ["dci", "bgp"]

    def acl_huawei(self, device: Any) -> str:
        if device.role.slug not in FABRIC_ROLES:
            return ""
        retired = _retired(device)
        peers = sorted({u["neighbour"] for u in uplinks(device)}
                       | {a for _, _, remote in links(device) for a in remote["loopbacks"].values()}
                       | set(retired.get("peers") or ()))
        ports = sorted(uplink_ports(device) | set(retired.get("ports") or ()))
        rows = ["bgp */\\d+/ %cant_delete=1",
                *(f"    peer {p} ~" for p in peers),
                "    ipv4-family unicast %cant_delete=1",
                *(f"        peer {p} ~" for p in peers),
                *self._networks(device, "        "),
                "    l2vpn-family evpn %cant_delete=1",
                *(f"        peer {p} ~" for p in peers),
                "        peer * split-group ~",
                "        peer * import reoriginate",
                "        peer * advertise route-reoriginated ~"]
        for port in ports:
            rows += [f"interface {port} %cant_delete=1", "    undo portswitch", "    description ~", "    mtu *",
                     "    ip address ~"]
        return "\n".join(rows)

    def _networks(self, device: Any, indent: str) -> list[str]:
        return [f"{indent}network {a} 255.255.255.255" for a in self._advertised(device)]

    def _advertised(self, device: Any) -> list[str]:
        own = loopback(device, "LoopBack0")
        vtep = loopback(device, vtep_loopback(device))
        return [str(a.ip) for a in (own, vtep) if a is not None]

    @staticmethod
    def _remote_evpn(address: str, split_group: str) -> Iterator[tuple]:
        yield "peer", address, "enable"
        yield "peer", address, "split-group", split_group
        yield "peer", address, "import reoriginate"
        yield "peer", address, "advertise route-reoriginated evpn mac-ip"
        yield "peer", address, "advertise route-reoriginated evpn mac"

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        found = links(device)
        if not found:
            return
        own = uplinks(device)
        for link, local, _ in found:
            for u in local["uplinks"].get(device.name, ()):
                address = ipaddress.IPv4Interface(u["address"])
                with self.block("interface", u["port"]):
                    yield "undo portswitch"
                    yield "description", f"DCI {link['name']} to {u['neighbour']}"
                    yield "mtu 9216"
                    yield "ip address", str(address.ip), str(address.netmask)
        remotes = [(remote, address) for _, _, remote in found for address in remote["loopbacks"].values()]
        reflectors = [address for _, address in evpn_peers(self.storage, device)]
        with self.block("bgp", fabric_asn(device)):
            for u in own:
                yield "peer", u["neighbour"], "as-number", u["asn"]
            for remote, address in remotes:
                yield "peer", address, "as-number", remote["asn"]
                yield "peer", address, "ebgp-max-hop 255"
                yield "peer", address, "connect-interface LoopBack0"
            with self.block("ipv4-family unicast"):
                for address in self._advertised(device):
                    yield "network", address, "255.255.255.255"
                for address in [u["neighbour"] for u in own] + [a for _, a in remotes]:
                    yield "peer", address, "enable"
            with self.block("l2vpn-family evpn"):
                for link, _, remote in found:
                    for address in remote["loopbacks"].values():
                        yield from self._remote_evpn(address, link["split_group"])
                for address in reflectors:
                    yield "peer", address, "import reoriginate"
                    yield "peer", address, "advertise route-reoriginated evpn mac-ip"
                    yield "peer", address, "advertise route-reoriginated evpn mac"
