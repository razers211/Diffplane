"""Patch logic for the platform's syslog loghost (V300R025).

A loghost is keyed by its address: retyping `info-center loghost <ip> ...` with another level or
port changes the line in place, and it is removed with `undo info-center loghost <ip> vpn-instance
<v>` (nothing after the VPN instance).
"""

from typing import Any

from annet.annlib.types import Op
from annet.rulebook import common


def loghost(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    if diff[Op.ADDED]:
        yield (True, diff[Op.ADDED][0]["row"], None)   # new, or changed in place
    elif diff[Op.REMOVED]:
        words = diff[Op.REMOVED][0]["row"].split()
        end = words.index("vpn-instance") + 2 if "vpn-instance" in words else 3
        yield (False, "undo " + " ".join(words[:end]), None)
