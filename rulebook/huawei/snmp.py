"""Diff logic for Huawei SNMP communities.

The switch shows a community as
    snmp-agent community read cipher <encrypted> alias <alias>
and the encrypted text changes on every write, so it can never be compared with the
plain-text community the Snmp generator emits. The generator names each community
with an alias derived from its value (see generators/snmp.py), so two rows describe
the same community when their access (read/write) and alias are equal.
"""

from typing import Any

from annet.annlib.rulebook.common import default_diff
from annet.annlib.types import Op


def community_key(row: str) -> tuple[str, str] | None:
    """(access, alias) of a community row, or None if it has no alias."""
    words = row.split()
    if len(words) < 4 or words[:2] != ["snmp-agent", "community"] or "alias" not in words[3:]:
        return None
    alias_at = words.index("alias", 3)
    if alias_at + 1 >= len(words):
        return None
    return words[2], words[alias_at + 1]


def community_diff(old: Any, new: Any, diff_pre: Any, _pops: Any = (Op.AFFECTED,)) -> list:
    diff = default_diff(old, new, diff_pre, _pops)
    removed = {community_key(item.row) for item in diff if item.op == Op.REMOVED}
    added = {community_key(item.row) for item in diff if item.op == Op.ADDED}
    same = (removed & added) - {None}
    return [item for item in diff if community_key(item.row) not in same]


# SNMPv3 (a test switch): a user's lines share its name as their key; removing the
# user (its bare line) is one `undo snmp-agent usm-user v3 <u>`, which takes its other lines with it.
def usm_user(rule: dict[str, Any], key: tuple[str, ...], diff: Any, **_: Any) -> Any:
    removed = [item["row"] for item in diff[Op.REMOVED]]
    if f"snmp-agent usm-user v3 {key[0]}" in removed:
        yield (False, f"undo snmp-agent usm-user v3 {key[0]}", None)
    else:
        for row in removed:
            yield (False, "undo " + row, None)
    for item in diff[Op.ADDED]:
        yield (True, item["row"], None)


# A trap target is keyed by its name and undone by it. Another address or reader retypes it under
# the same name (an undo would be sorted after the retype and remove it): replacing in place is
# unverified until the lab moves one.
def target_host(rule: dict[str, Any], key: tuple[str, ...], diff: Any, **_: Any) -> Any:
    if diff[Op.ADDED]:
        yield (True, diff[Op.ADDED][0]["row"], None)
    elif diff[Op.REMOVED]:
        yield (False, f"undo snmp-agent target-host host-name {key[0]}", None)


# A v3 group is keyed by name and security level and undone with nothing after them.
def group(rule: dict[str, Any], key: tuple[str, ...], diff: Any, **_: Any) -> Any:
    if diff[Op.REMOVED] and not diff[Op.ADDED]:
        yield (False, f"undo snmp-agent group v3 {key[0]} {key[1]}", None)
    for item in diff[Op.ADDED]:
        yield (True, item["row"], None)



VERSIONS = ("v1", "v2c", "v3")


def _enabled(rows: list[str]) -> set[str] | None:
    """Versions on, from `snmp-agent sys-info version ...` rows (`all`, a list, or `<v> disable`)."""
    on: set[str] = set()
    for row in rows:
        args = row.split()[3:]
        disable = args[-1:] == ["disable"]
        names = set(VERSIONS) if "all" in args else set(args) - {"disable"}
        if not names <= set(VERSIONS):
            return None
        on = on - names if disable else on | names
    return on


def version(rule: dict[str, Any], key: tuple[str, ...], diff: Any, **kwargs: Any) -> Any:
    """Only what changes: `version <v> disable` for each version going off, then the ones coming on
    (adding keeps the others). Annet's rule sends `version all disable` first, which V300
    refuses while a target host uses v3 and otherwise asks a question; it also joined a set, so its order changed between runs."""
    old = _enabled([a["row"] for a in diff[Op.REMOVED]])
    new = _enabled([a["row"] for a in diff[Op.ADDED]])
    if not diff[Op.REMOVED] or not diff[Op.ADDED] or old is None or not new:
        from annet.rulebook.huawei.misc import snmpagent_sysinfo_version

        for direct, row, children in snmpagent_sysinfo_version(rule, key, diff, **kwargs):
            words = row.split()
            if words[3:] and set(words[3:]) <= set(VERSIONS):   # Annet joins a set: a fixed order
                row = " ".join(words[:3] + sorted(words[3:], key=VERSIONS.index))
            yield direct, row, children
        return
    for name in sorted(old - new, key=VERSIONS.index):
        yield True, f"snmp-agent sys-info version {name} disable", None
    if on := sorted(new - old, key=VERSIONS.index):
        yield True, "snmp-agent sys-info version " + " ".join(on), None
