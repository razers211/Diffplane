"""Diff logic for Huawei M-LAG (DFS group).

VRP stores the DFS group key encrypted (`authentication-mode hmac-sha256 password
%^%#...`), so it never equals the plain key the Mlag generator emits. With no alias to
compare by, both forms count as equal when both are present: the key being set is
checked, its value is not.
"""

from typing import Any

from annet.annlib.rulebook.common import default_diff
from annet.annlib.types import Op

KEY_PREFIX = "authentication-mode hmac-sha256 password "


def password_presence(old: Any, new: Any, diff_pre: Any, _pops: Any = (Op.AFFECTED,)) -> list:
    diff = default_diff(old, new, diff_pre, _pops)
    removed = [item for item in diff if item.op == Op.REMOVED and item.row.startswith(KEY_PREFIX)]
    added = [item for item in diff if item.op == Op.ADDED and item.row.startswith(KEY_PREFIX)]
    if removed and added:
        drop = {id(item) for item in removed + added}
        return [item for item in diff if id(item) not in drop]
    return diff
