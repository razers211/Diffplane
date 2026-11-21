"""What may go on by itself at a window's start: only additions of the
approved command shapes, never a removal, nothing on fabric links, the peer-link, routing or management."""

from orchestrator.windows import lines, safe_difference

APPROVED = """interface 10GE1/0/5
  storm-control broadcast min-rate percent 1 max-rate percent 1
  quit
interface Eth-Trunk3
  description server-a
  quit
"""
GUARDED = {"100GE1/0/1", "Eth-Trunk0", "10GE1/0/47"}


def with_lines(extra: str) -> str:
    return APPROVED + extra


def test_the_same_patch_is_safe():
    assert safe_difference(APPROVED, APPROVED, GUARDED) is None


def test_the_same_command_on_another_port_is_safe():
    new = with_lines("interface 10GE1/0/9\n  storm-control broadcast min-rate percent 1 max-rate percent 1\n  quit\n")
    assert safe_difference(APPROVED, new, GUARDED) is None


def test_a_removal_is_not():
    new = with_lines("interface 10GE1/0/9\n  undo storm-control broadcast\n  quit\n")
    assert "removal" in safe_difference(APPROVED, new, GUARDED)


def test_another_kind_of_command_is_not():
    new = with_lines("interface 10GE1/0/9\n  shutdown\n  quit\n")
    assert "kind the approved patch does not have" in safe_difference(APPROVED, new, GUARDED)


def test_fabric_links_peer_link_and_management_are_not():
    for port in ("100GE1/0/1", "Eth-Trunk0", "10GE1/0/47", "MEth0/0/0"):
        new = with_lines(f"interface {port}\n  storm-control broadcast min-rate percent 1 max-rate percent 1\n  quit\n")
        assert "fabric link" in safe_difference(APPROVED, new, GUARDED), port


def test_routing_is_not_even_when_its_shape_was_approved():
    approved = "ip route-static 10.0.0.0 255.0.0.0 NULL0\n"
    new = approved + "ip route-static 11.0.0.0 255.0.0.0 NULL0\n"
    assert "routing" in safe_difference(approved, new, GUARDED)


def test_lines_keep_their_blocks_and_drop_quit():
    assert lines(APPROVED)[:2] == [("interface 10GE1/0/5",),
                                   ("interface 10GE1/0/5", "storm-control broadcast min-rate percent 1 max-rate percent 1")]
    assert ("quit",) not in lines(APPROVED)


# -- a fabric's usual window ---------------------------------------------------------------------

import json
from datetime import datetime, timezone

import pytest

from orchestrator.windows import check_all, check_usual, next_usual, outside_usual

USUAL = {"days": ["sat", "tue"], "start": "02:00", "minutes": 180, "zone": "Europe/Paris"}


def test_a_usual_window_is_checked_and_kept_in_day_order():
    assert check_usual(USUAL)["days"] == ["tue", "sat"]
    for bad, why in (({**USUAL, "days": []}, "days"), ({**USUAL, "start": "25:00"}, "start"),
                     ({**USUAL, "minutes": 5}, "minutes"), ({**USUAL, "zone": "Mars/Base"}, "zone")):
        with pytest.raises(ValueError, match=why):
            check_usual(bad)
    assert json.loads(check_all(json.dumps({"example": USUAL}))) == {"example": check_usual(USUAL)}


def test_the_next_usual_window_follows_the_zone_and_daylight_saving():
    # Wednesday: the next is Saturday 24th 02:00 Paris (summer time, UTC+2) = 00:00 UTC;
    # a week later Paris is on winter time (UTC+1): 02:00 Paris = 01:00 UTC.
    wednesday = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    assert next_usual(USUAL, wednesday) == (datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc),
                                            datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc))
    after = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)   # Tuesday afternoon: Saturday 31st next
    assert next_usual(USUAL, after)[0] == datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)


def test_a_window_outside_the_usual_one_is_noticed():
    start = datetime(2026, 10, 31, 1, 30, tzinfo=timezone.utc)
    assert not outside_usual(USUAL, start, start.replace(hour=3))
    assert outside_usual(USUAL, start, start.replace(hour=5))   # runs past 04:00 UTC
    assert outside_usual(USUAL, start.replace(day=30), start.replace(day=30, hour=3))   # a Friday
