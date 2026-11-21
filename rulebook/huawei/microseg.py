"""Patch logic for microsegmentation rules (V300R025-8).

A segment classifier's rule is keyed by its number. Typing `rule <n> ...` over an existing rule
changes it in place ("Info: The rule 10 has been modified."); `undo rule <n>` alone is
"Incomplete": a rule is removed with its whole line, `undo rule <n> permit ...`.
"""

from typing import Any

from annet.annlib.rulebook.common import default_diff
from annet.annlib.types import Op
from annet.rulebook import common


def rule(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    if diff[Op.ADDED]:
        yield (True, diff[Op.ADDED][0]["row"], None)   # new, or changed in place
    elif diff[Op.REMOVED]:
        yield (False, "undo " + diff[Op.REMOVED][0]["row"], None)


DEFAULT_DENY = "traffic-segment default-policy deny"


def default_deny(old: Any, new: Any, diff_pre: Any, _pops: Any = (Op.AFFECTED,)) -> list:
    """`traffic-segment default-policy deny` is the default: typed, it changes nothing and is
    never displayed (eNSP V300R025C10, 2026-11-21), so a switch without it counts as having it."""
    return [item for item in default_diff(old, new, diff_pre, _pops)
            if not (item.op == Op.ADDED and item.row.strip() == DEFAULT_DENY)]
