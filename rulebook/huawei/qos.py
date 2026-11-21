"""Patch logic for QoS rules (V300R025-4).

`trust dscp` is removed with `undo trust` ("Too many parameters" with `dscp`); `undo trust ?`
offers only `upstream` and `<cr>`.
"""

from typing import Any

from annet.annlib.types import Op
from annet.rulebook import common


def trust(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    if diff[Op.ADDED]:
        yield (True, diff[Op.ADDED][0]["row"], None)
    elif diff[Op.REMOVED]:
        yield (False, "undo trust", None)
