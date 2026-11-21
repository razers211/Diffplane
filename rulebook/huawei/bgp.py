"""BGP patch logic overrides: peers of a BGP-VPN instance (`bgp` / `vpn-instance <v>`).

V300 cannot change a peer's AS in place and removes a peer only with `undo peer <ip>`, which
deletes every line of that peer (no-commit CLI test). Annet
already handles this for peers at the top of `bgp` and in address families
(annet.rulebook.huawei.bgp: `peer`, `reapply_after_as_number_change`), but its rulebook gives a
`vpn-instance` block no peer rules, so a firewall ASN change was patched as
`undo peer <ip> as-number <old>` and refused. Our rulebook (texts/huawei.rul) adds those rules.

The wipe also takes the peer's lines in the instance's own family block
(`ipv4-family vpn-instance <v>`, e.g. `peer <ip> enable`), a sibling of the `vpn-instance`
block that Annet's rewrite never visits. Here a re-created peer has those lines sent again, and
a removed peer's removals there are dropped (the peer no longer exists to undo them on).
"""

from typing import Any

from annet.annlib.types import Op
from annet.rulebook.huawei import bgp as annet_bgp


def reapply_after_as_number_change(old, new, diff_pre, _pops: tuple[str, ...] = (Op.AFFECTED,)) -> list[Any]:
    items = annet_bgp.reapply_after_as_number_change(old, new, diff_pre, _pops)
    result = []
    for item in items:   # one per `bgp <asn>` row
        wiped = _vpn_peers_wiped(old.get(item.row, {}), new.get(item.row, {}))
        if wiped:
            item = item._replace(children=[_family(child, wiped) for child in item.children])
        result.append(item)
    return result


def _vpn_peers_wiped(bgp_old: dict, bgp_new: dict) -> dict[str, tuple[set[str], set[str]]]:
    """Per BGP-VPN instance: (IPs whose peer is wiped, those of them declared again)."""
    found = {}
    for row, block in bgp_old.items():
        tokens = row.split()
        if len(tokens) != 2 or tokens[0] != "vpn-instance":
            continue
        new_block = bgp_new.get(row, {})
        wiped = annet_bgp._wiped_peer_ips(block, new_block)
        if wiped:
            found[tokens[1]] = (wiped, wiped & annet_bgp._peer_ips_with_as_number(new_block))
    return found


def _family(item, wiped: dict[str, tuple[set[str], set[str]]]):
    tokens = item.row.split()
    if len(tokens) != 3 or tokens[0] not in ("ipv4-family", "ipv6-family") or tokens[1] != "vpn-instance" \
            or tokens[2] not in wiped:
        return item
    gone, again = wiped[tokens[2]]
    children = []
    for child in item.children:
        key = annet_bgp._peer_key(child.row)
        if key in gone and child.op == Op.REMOVED:
            continue
        if key in again and child.op == Op.AFFECTED:
            child = child._replace(op=Op.ADDED)
        children.append(child)
    return item._replace(children=children)
