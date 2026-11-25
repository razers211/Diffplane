"""A switch whose patch differs at a window's start: no switch is ever
skipped; the window goes on by itself only when the difference is safe, else an approver is asked.

`safe_difference` is the part that reads the two patches: every line beyond the approved patch
must be an addition of the same command shape as an approved line (the same
command with only names and numbers different, in the same kind of block), never a removal, and
nothing on fabric links, the peer-link, routing or management. The rest of the rule (a deploy
verified since the approval, no one else's undeployed intent, the pre-deploy checks) is the job
runner's (orchestrator/deploys.py).
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from generators import naming

# Top-level blocks the window never changes beyond its approved lines: routing and management.
GUARDED_BLOCKS = re.compile(
    r"(bgp|ospf|ospfv3|isis|route-policy|ip (ip-prefix|ipv6-prefix|as-path-filter|community-filter|route-static)"
    r"|ipv6 route-static|aaa|ip vpn-instance _management_vpn_|ssh|stelnet|snetconf|netconf|user-interface)\b")
_NUMBERED = re.compile(r"\S*\d\S*")


def lines(patch: str) -> list[tuple[str, ...]]:
    """Each command of a patch with the block lines above it (Annet's `quit` lines left out)."""
    out, stack = [], []   # stack: (indent, line)
    for raw in patch.splitlines():
        text = raw.strip()
        if not text or text == "quit":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, text))
        out.append(tuple(t for _, t in stack))
    return out


def shape(path: tuple[str, ...]) -> tuple[str, ...]:
    """A command with every word holding a digit (names of ports, numbers, addresses) made alike."""
    return tuple(_NUMBERED.sub("#", line) for line in path)


def _port(path: tuple[str, ...]) -> str | None:
    words = path[0].split()
    return words[1] if len(words) >= 2 and words[0] == "interface" else None


def safe_difference(approved: str, new: str, guarded_ports: set[str]) -> str | None:
    """Why the lines of `new` beyond `approved` may not go out without an approver; None if they may."""
    extra = Counter(lines(new)) - Counter(lines(approved))
    if not extra:
        return None
    approved_shapes = {shape(p) for p in lines(approved)}
    for path in extra:
        command = " / ".join(path)
        if any(line.startswith("undo ") for line in path):
            return f"a removal that was not approved: {command}"
        if GUARDED_BLOCKS.match(path[0]):
            return f"a change to routing or management: {command}"
        port = _port(path)
        if port is not None and (port.split(".")[0] in guarded_ports or naming.is_management(port.split(".")[0])):
            return f"a change on a fabric link, the peer-link or management: {command}"
        if shape(path) not in approved_shapes:
            return f"a change of a kind the approved patch does not have: {command}"
    return None


# -- a fabric's usual window ----------------------------------------------------
# Days, a start and a length in a named time zone (so it follows daylight saving), set by approvers
# and admins; offered by default when scheduling, a window outside it only warns. Kept in the
# platform setting CHANGE_WINDOWS: {fabric: {"days": ["tue", "sat"], "start": "02:00", "minutes": 180,
# "zone": "Africa/Algiers"}}.

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def check_usual(usual: dict) -> dict:
    """A usual window as stored, or ValueError saying what is wrong."""
    if not isinstance(usual, dict) or set(usual) != {"days", "start", "minutes", "zone"}:
        raise ValueError("a usual window has days, start, minutes and zone")
    days = usual["days"]
    if not isinstance(days, list) or not days or any(d not in DAYS for d in days):
        raise ValueError(f"days: one or more of {', '.join(DAYS)}")
    if not isinstance(usual["start"], str) or not _HHMM.fullmatch(usual["start"]):
        raise ValueError("start: HH:MM, 24-hour")
    if not isinstance(usual["minutes"], int) or not 15 <= usual["minutes"] <= 24 * 60:
        raise ValueError("minutes: from 15 to 1440")
    try:
        ZoneInfo(usual["zone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError(f"zone: {usual['zone']!r} is not a time zone name (e.g. Europe/Paris)") from None
    return {"days": [d for d in DAYS if d in days], "start": usual["start"], "minutes": usual["minutes"],
            "zone": usual["zone"]}


def check_all(value: str) -> str:
    """The CHANGE_WINDOWS setting: every fabric's usual window checked."""
    try:
        windows = json.loads(value or "{}")
    except ValueError:
        raise ValueError("usual windows: not JSON") from None
    if not isinstance(windows, dict):
        raise ValueError("usual windows: one per fabric")
    return json.dumps({site: check_usual(w) for site, w in sorted(windows.items())}, separators=(",", ":"))


def usual_windows(env=os.environ) -> dict[str, dict]:
    return json.loads(env.get("CHANGE_WINDOWS") or "{}")


def occurrences(usual: dict, around: datetime, days: int = 9) -> list[tuple[datetime, datetime]]:
    """The usual window's openings (UTC) from the day before `around` for `days` days."""
    zone = ZoneInfo(usual["zone"])
    hour, minute = map(int, usual["start"].split(":"))
    first = around.astimezone(zone).date() - timedelta(days=1)
    out = []
    for n in range(days):
        day = first + timedelta(days=n)
        if DAYS[day.weekday()] in usual["days"]:
            start = datetime.combine(day, time(hour, minute), zone).astimezone(around.tzinfo)
            out.append((start, start + timedelta(minutes=usual["minutes"])))
    return out


def next_usual(usual: dict, at: datetime) -> tuple[datetime, datetime] | None:
    """The next usual window that has not ended (it may be open now)."""
    return next(((s, e) for s, e in occurrences(usual, at) if e > at), None)


def outside_usual(usual: dict, start: datetime, end: datetime) -> bool:
    return not any(s <= start and end <= e for s, e in occurrences(usual, start))
