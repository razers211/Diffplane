import os
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import (description, device_value, dfs_priority, fabric_value, has_overlay, loopback, mlag_peer_name,
                               mlag_trunks, peer_link, related, trunk_description)

DFS_GROUP = 1
SECRET_ENV = "MLAG_DFS_PASSWORD"


class MissingDfsPassword(Exception):
    pass


class Mlag(PartialGenerator):
    """M-LAG on a leaf: V-STP, the DFS group, the peer-link and server trunk bindings.

    DFS heartbeat from the own LoopBack0 to the peer's; priority by hostname order.
    The DFS key comes from MLAG_DFS_PASSWORD. VRP stores it encrypted, so our rulebook
    only checks that a key is set (rulebook/huawei/mlag.py): a key change is not a diff.
    Verified on the lab switch (V300R023).
    """

    TAGS = ["mlag"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device) or mlag_peer_name(device) is None:
            return ""
        lines = ["stp v-stp enable", "dfs-group ~", "    ~ %global"]
        trunks = [t for t in [peer_link(device), *mlag_trunks(device)] if t is not None]
        for trunk in trunks:
            lines += [
                # We own these trunks entirely, so they may be deleted (not just emptied).
                f"interface {trunk.name}  %cant_delete=0",
                "    description ~",
                "    mode ~",
                "    peer-link ~",
                "    port vlan exclude ~",
                "    dfs-group ~",
            ]
            for member in trunk.members:
                lines += [f"interface {member}", "    eth-trunk ~"]
        return "\n".join(lines)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        peer_name = mlag_peer_name(device)
        if not has_overlay(device) or peer_name is None:
            return
        keyed = fabric_value(device, "dfs_authentication", True)   # an imported fabric's pairs have no key
        password = os.environ.get(SECRET_ENV)
        if keyed and not password:
            raise MissingDfsPassword(f"{SECRET_ENV} is not set; it is required for M-LAG leaves")
        own = loopback(device, "LoopBack0")
        peer = loopback(related(self.storage, [peer_name])[peer_name], "LoopBack0")
        # imported fabrics: the heartbeat over MEth in _management_vpn_, adopted as the switches have it
        heartbeat = device_value(device, "heartbeat", None)

        yield "stp v-stp enable"
        group = fabric_value(device, "dfs_group", DFS_GROUP)
        with self.block("dfs-group", group):
            if (check := fabric_value(device, "dfs_consistency_check", None)) is not None:
                yield "consistency-check enable mode", check
            if keyed:
                yield "authentication-mode hmac-sha256 password", password
            if heartbeat is not None:
                yield ("dual-active detection source ip", heartbeat["source"], "vpn-instance", heartbeat["vpn"],
                       "peer", heartbeat["peer"])
            else:
                yield "dual-active detection source ip", str(own.ip), "peer", str(peer.ip)
            if (priority := dfs_priority(device, peer_name)) != 100:   # VRP's default: never displayed, so never
                yield "priority", priority                      # in the running config (CE6866, 2026-11-19)

        link = peer_link(device)
        if link is not None:
            with self.block("interface", link.name):
                yield "description", description(device, link.name, f"peer-link to {peer_name}")
                yield "mode lacp-static"
                yield "peer-link 1"
                yield "port vlan exclude 1"
            for member in link.members:
                with self.block("interface", member):
                    yield "eth-trunk", link.number

        for trunk in mlag_trunks(device):
            with self.block("interface", trunk.name):
                if (text := trunk_description(device, trunk.name)) is not None:
                    yield "description", text
                yield "mode lacp-static"
                yield "dfs-group", group, "m-lag", trunk.number
            for member in trunk.members:
                with self.block("interface", member):
                    yield "eth-trunk", trunk.number
