"""Patch logic for a typed RSA peer public key (V300R025).

V300 stores the key's hex nested by its ASN.1 structure (sequence header, then each integer's
header, then its content), so the running config's block has three levels under `public-key-code
begin`. Annet's Huawei rule (`%multiline`) compares the block whole, but its formatter would type a
`quit` after each nested level, which the key view would read as hex. The key view takes the hex
as plain lines, so the block's body is sent flat, in its order, with no `quit`.
"""

from collections import OrderedDict
from typing import Any

from annet.annlib.types import Op
from annet.rulebook import common

OPS = (Op.ADDED, Op.REMOVED, Op.MOVED, Op.AFFECTED, Op.UNCHANGED)


def _rows(pre: dict) -> list[tuple[dict, str]]:
    """Every body row in order, depth first, with the rule entry it came from."""
    found = []
    for entry in pre.values():
        for item in entry["items"].values():
            for op in OPS:
                for each in item[op]:
                    found.append((entry, op, each["row"]))
                    found += _rows(each["children"] or {})
    return found


def _flat(pre: dict) -> dict:
    flat: dict = OrderedDict()
    for position, (entry, op, row) in enumerate(_rows(pre)):
        target = flat.setdefault(entry["rule"], {"rule": entry["rule"], "attrs": entry["attrs"],
                                                 "items": OrderedDict(), "positions": {}})
        # a row repeated in the key's hex keeps its own place: the key is its position
        target["items"][(row, position)] = {o: [] for o in OPS}
        target["items"][(row, position)][op].append({"row": row, "children": OrderedDict()})
        target["positions"][(row, position)] = position
    return flat


def flat(rule: dict[str, Any], key: tuple[str, ...], diff: common.DiffDict, **kwargs: Any) -> common.LogicResult:
    for direction, row, children in common.default(rule, key, diff, **kwargs):
        yield direction, row, _flat(children) if direction and children else children
