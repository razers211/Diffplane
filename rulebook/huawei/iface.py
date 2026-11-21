"""Interface patch logic overrides."""

import re
from typing import Any

from annet.annlib.types import Op
from annet.rulebook.huawei import iface as annet_iface
from annet.rulebook import common

from rulebook.huawei.vpn import (bound_instance, removed_ipv6_families, removed_segment_instances, without_removals,
                                 without_removed_rows)


def permanent(rule: dict[str, Any], key: tuple[str, ...], diff: Any, **kwargs: Any):
    """Annet's Huawei rule, plus NVE interfaces as removable (`undo interface Nve1`).

    Annet treats every interface it does not know as permanent (only its contents can
    be removed); an NVE interface is a logical interface and can be deleted.

    An interface bound to a segment VPN instance that the patch removes loses the binding with
    the instance, before this block (rulebook.huawei.vpn): its `undo ip binding` is left out.
    An interface bound to a segment instance whose IPv6 family the patch removes (or the instance
    itself) loses its IPv6 address with it: its `undo ipv6 address` is left out.
    """
    if re.fullmatch(r"Nve\d+", key[0]):
        logic = common.default(rule, key, diff, **kwargs)
    else:
        logic = annet_iface.permanent(rule, key, diff, **kwargs)
    root_pre = kwargs.get("root_pre")
    gone = removed_segment_instances(root_pre)
    no_ipv6 = gone | removed_ipv6_families(root_pre)
    rows = {f"ip binding vpn-instance {v}" for v in gone}
    for direct, row, sub_pre in logic:
        if sub_pre:
            sub_pre = without_removals(sub_pre, rows)
            if no_ipv6 and bound_instance(sub_pre) in no_ipv6:
                sub_pre = without_removed_rows(sub_pre, lambda r: not r.startswith("ipv6 address "))
        yield direct, row, sub_pre


def bridge_domain(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    """An L2 sub-interface leaves its bridge domain with `undo bridge-domain`, without the number
    (a test switch, V300R025: `undo bridge-domain 1` is "Too many parameters"), before it
    joins another (joining while in one is refused). Accepted also after the old bridge domain
    was removed earlier in the same candidate, as Annet's order sends it."""
    for _action in diff[Op.REMOVED]:
        yield (False, "undo bridge-domain", None)
    for action in diff[Op.ADDED]:
        yield (True, action["row"], None)


def speed(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    """A copper port's fixed speed is undone as `undo speed`, nothing after it (a lab test
    under commit trial: accepted with `negotiation disable` still there)."""
    for _action in diff[Op.REMOVED]:
        yield (False, "undo speed", None)
    for action in diff[Op.ADDED]:
        yield (True, action["row"], None)
