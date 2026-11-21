"""Patch logic for Huawei DHCP relay lines.

V300 removes a VBDIF's relay source interface with `undo dhcp relay giaddr source-interface`,
without the interface's name: `undo dhcp relay giaddr source-interface LoopBack2` is refused
("Too many parameters", a lab deploy; console help confirmed the
other relay lines undo in full).
"""

from typing import Any

from annet.annlib.types import Op
from annet.rulebook import common


def giaddr(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    for _action in diff[Op.REMOVED]:
        yield (False, "undo dhcp relay giaddr source-interface", None)
    for action in diff[Op.ADDED]:
        yield (True, action["row"], None)


def source_interface6(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    """DHCPv6 relay: `undo dhcpv6 relay source-interface` takes no name either (a test switch
    console help: `<cr>` right after it)."""
    for _action in diff[Op.REMOVED]:
        yield (False, "undo dhcpv6 relay source-interface", None)
    for action in diff[Op.ADDED]:
        yield (True, action["row"], None)
