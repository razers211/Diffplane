import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import device_value, has_vtep, loopback, mlag_peer_name, pip_loopback, related, vtep_loopback

NVE = "Nve1"


def nve_mac(vtep: ipaddress.IPv4Address) -> str:
    """Per-pair NVE MAC, derived from the shared VTEP address: same on both leaves of a
    pair, unique per pair. 02 = locally administered unicast. VRP format hhhh-hhhh-hhhh."""
    digits = "02" + "00" + vtep.packed.hex()
    return "-".join(digits[i:i + 4] for i in range(0, 12, 4))


class Nve(PartialGenerator):
    """VXLAN tunnel endpoint of a leaf.

    `source` is the VTEP address on LoopBack1: the pair's shared one on an M-LAG leaf, with
    `pip-source` (own LoopBack0, the peer's LoopBack0, `bypass`; LoopBack2 and LoopBack1 in the
    PIP layout) and the pair's NVE MAC, as on
    ref-leaf1; a lone leaf's own, with `source` only (unverified on a switch). VNI
    membership belongs to the segment generators, not here.
    """

    TAGS = ["vxlan", "nve"]

    def acl_huawei(self, device: Any) -> str:
        if not has_vtep(device):
            return ""
        return "\n".join(
            [
                "evpn-overlay enable",
                f"interface {NVE}  %cant_delete=0",  # owned entirely: may be deleted
                "    source ~",
                "    pip-source ~",
                "    mac-address ~",
            ]
        )

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_vtep(device):
            return
        vtep = loopback(device, vtep_loopback(device))
        peer_name = mlag_peer_name(device)

        yield "evpn-overlay enable"
        with self.block("interface", NVE):
            yield "source", str(vtep.ip)
            if peer_name is not None:   # M-LAG pair only
                own = loopback(device, pip_loopback(device))
                peer = loopback(related(self.storage, [peer_name])[peer_name], pip_loopback(device))
                yield "pip-source", str(own.ip), "peer", str(peer.ip), "bypass"
                yield "mac-address", device_value(device, "nve_mac", nve_mac(vtep.ip))
