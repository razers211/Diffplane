"""Patch logic for OSPF authentication (V300R025)."""

from typing import Any

from annet.annlib.rulebook.common import default_diff
from annet.annlib.types import Op
from annet.rulebook import common
from annet.rulebook.huawei.aaa import local_user_diff

KEY_PREFIX = "key-string cipher "


def presence(prefix: str, old: Any, new: Any, diff_pre: Any, pops: Any) -> list:
    """VRP stores a key encrypted, so it never equals the plain key generated."""
    diff = default_diff(old, new, diff_pre, pops)
    removed = [item for item in diff if item.op == Op.REMOVED and item.row.startswith(prefix)]
    added = [item for item in diff if item.op == Op.ADDED and item.row.startswith(prefix)]
    if removed and added:
        drop = {id(item) for item in removed + added}
        return [item for item in diff if id(item) not in drop]
    return diff


def key_presence(old: Any, new: Any, diff_pre: Any, _pops: Any = (Op.AFFECTED,)) -> list:
    return presence(KEY_PREFIX, old, new, diff_pre, _pops)


def radius_key_presence(old: Any, new: Any, diff_pre: Any, _pops: Any = (Op.AFFECTED,)) -> list:
    return presence("radius-server shared-key cipher ", old, new, diff_pre, _pops)


def local_user_presence(old: Any, new: Any, diff_pre: Any, **kwargs: Any) -> list:
    """Stored hashed, and the user changes it at first login: an account's password is never compared."""
    diff = local_user_diff(old, new, diff_pre, **kwargs)
    prefixes = {" ".join(item.row.split()[:3]) for item in diff if item.op == Op.ADDED and " password " in item.row}
    both = {p for p in prefixes
            if any(item.op == Op.REMOVED and item.row.startswith(p + " ") for item in diff)}
    return [item for item in diff if " ".join(item.row.split()[:3]) not in both]


def keychain(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    """`undo keychain <name>`, without the mode."""
    for _action in diff[Op.REMOVED]:
        yield (False, f"undo keychain {key[0]}", None)
    for action in diff[Op.ADDED]:
        yield (True, action["row"], action["children"] if "children" in action else None)
    for action in diff[Op.AFFECTED]:
        yield (True, action["row"], action["children"])


def interface(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **_: Any) -> common.LogicResult:
    """`undo ospf authentication-mode` takes nothing after it."""
    for _action in diff[Op.REMOVED]:
        yield (False, "undo ospf authentication-mode", None)
    for action in diff[Op.ADDED]:
        yield (True, action["row"], None)
