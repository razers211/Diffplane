"""Removing a segment's VPN instance (`<name>-<bd>`) before anything else.

A renamed segment's new instance takes the old one's RD and L3 VNI, and V300 refuses an RD still
used by another instance as soon as the line is typed ("Route distinguisher is not unique",
a test switch deploy). So the old instance is removed first (texts/huawei.order). On
V300, `undo ip vpn-instance <v>` also removes the instance's BGP-VPN blocks (`vpn-instance <v>`
and `ipv4-family vpn-instance <v>` in `bgp`): undoing them afterwards fails with "The VPN
instance name is not configured", and so does an interface's `undo ip binding vpn-instance <v>` (no-commit CLI tests). Those removals are left out of the patch
here; VRP makes them itself. Routes leave before the instance (removals without an ordering rule
of their own sort first), as before.

`undo ipv6-family` in a segment or service instance (IPv6 turned off, the instance kept) likewise removes
the instance's BGP `ipv6-family vpn-instance <v>` and the IPv6 addresses of interfaces bound to it,
keeping their `ipv6 enable` and `ipv6 nd` lines (a test switch: a deploy failed at `undo ipv6 address`
with "The specified IPv6 address does not exist"; `display configuration candidate merge` after
the undo). Those removals are left out too.
"""

import re
from typing import Any

from annet.annlib.types import Op
from annet.rulebook.huawei import bgp as annet_bgp

SYSTEM_VPN = ("_management_vpn_", "_public_")
SEGMENT_VPN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*-\d+")   # generators/fabric.py SEGMENT_VPN_PATTERN


def removed_segment_instances(root_pre: Any) -> set[str]:
    """Segment VPN instances the patch removes whole (`undo ip vpn-instance <v>`)."""
    names = set()
    for content in (root_pre or {}).values():
        for diff in content["items"].values():
            if diff[Op.ADDED] or diff.get(Op.AFFECTED):
                continue
            for item in diff[Op.REMOVED]:
                words = item["row"].split()
                if len(words) == 3 and words[:2] == ["ip", "vpn-instance"] and SEGMENT_VPN.fullmatch(words[2]):
                    names.add(words[2])
    return names


def removed_ipv6_families(root_pre: Any) -> set[str]:
    """Platform VPN instances (segments' and services') whose `ipv6-family` the patch removes while the
    instance stays."""
    names = set()
    for content in (root_pre or {}).values():
        for diff in content["items"].values():
            for item in diff.get(Op.AFFECTED) or []:
                words = item["row"].split()
                if len(words) != 3 or words[:2] != ["ip", "vpn-instance"] or words[2] in SYSTEM_VPN:
                    continue
                children = item.get("children") or {}
                if any(child["row"] == "ipv6-family" for sub in children.values() for d in sub["items"].values()
                       for child in d[Op.REMOVED]):
                    names.add(words[2])
    return names


def bound_instance(pre: Any) -> str | None:
    """The VPN instance an interface block is bound to (its `ip binding vpn-instance` row)."""
    for content in (pre or {}).values():
        for diff in content["items"].values():
            for items in diff.values():
                for item in items or []:
                    words = item["row"].split()
                    if len(words) == 4 and words[:3] == ["ip", "binding", "vpn-instance"]:
                        return words[3]
    return None


def without_removed_rows(pre: Any, keep) -> Any:
    """`pre` without the whole removals whose rows `keep(row)` refuses."""
    if not pre:
        return pre
    result = type(pre)()
    for raw_rule, content in pre.items():
        items = type(content["items"])()
        for key, diff in content["items"].items():
            removed = diff[Op.REMOVED] and not diff[Op.ADDED] and not diff.get(Op.AFFECTED)
            if removed and not any(keep(item["row"]) for item in diff[Op.REMOVED]):
                continue
            items[key] = diff
        if items:
            result[raw_rule] = {**content, "items": items}
    return result


def without_removals(pre: Any, rows: set[str]) -> Any:
    """`pre` without the removal of these rows (whole removals only; additions stay)."""
    if not pre or not rows:
        return pre
    result = type(pre)()
    for raw_rule, content in pre.items():
        items = type(content["items"])()
        for key, diff in content["items"].items():
            removed = diff[Op.REMOVED] and not diff[Op.ADDED] and not diff.get(Op.AFFECTED)
            if removed and all(item["row"] in rows for item in diff[Op.REMOVED]):
                continue
            items[key] = diff
        if items:
            result[raw_rule] = {**content, "items": items}
    return result


def bgp(rule: dict[str, Any], key: tuple[str, ...], diff: Any, root_pre: Any = None, **kwargs: Any):
    """Annet's `bgp` logic (`undo bgp` then commit), without the BGP-VPN blocks of segment
    instances removed in the same patch."""
    gone = removed_segment_instances(root_pre)
    rows = {f"vpn-instance {v}" for v in gone} | {f"ipv4-family vpn-instance {v}" for v in gone}
    rows |= {f"ipv6-family vpn-instance {v}" for v in gone | removed_ipv6_families(root_pre)}
    for direct, row, sub_pre in annet_bgp.undo_commit(rule, key, diff, root_pre=root_pre, **kwargs):
        if sub_pre and rows:
            sub_pre = without_removals(sub_pre, rows)
            if not _changes(sub_pre):
                continue   # nothing left to do in bgp (an empty `bgp` block was sent)
        yield direct, row, sub_pre


def _changes(pre: Any) -> bool:
    """Whether anything in `pre` is added, removed, changed or moved (unchanged rows are kept
    in it as context)."""
    return any(diff.get(op) for content in (pre or {}).values() for diff in content["items"].values()
               for op in (Op.ADDED, Op.REMOVED, Op.AFFECTED, Op.MOVED))
