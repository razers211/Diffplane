"""Patches for microsegmentation, both directions, with our rulebook: the policy objects
first, EPGs after the VPN instances and bridge domains their members name; removal in the order
leaf1 accepted: policy, classifiers and behaviors, EPGs, enable."""

from pathlib import Path

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

GOLDEN = Path(__file__).parents[1] / "golden"
BEFORE = (GOLDEN / "example-ipv6" / "leaf1.cfg").read_text()       # dual-stack segments, no microsegmentation
AFTER = (GOLDEN / "example-microseg" / "leaf1.cfg").read_text()    # the same in Learn with two contracts
EMPTY = (GOLDEN / "example" / "leaf1.cfg").read_text()             # no segments at all


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "quit"]


def test_turning_it_on_enables_first_and_makes_epgs_after_their_instances_and_bridge_domains():
    result = lines(patch(EMPTY, AFTER))
    assert result.index("traffic-segment enable") < result.index("segment behavior GBP-1-2")
    assert result.index("segment classifier GBP-1-2") < result.index("segment policy FABRIC") < result.index("segment-policy FABRIC")
    epg = result.index("traffic-segment segment-id 1 segment-name web intra-epg-behavior permit")
    assert result.index("ip vpn-instance web-500") < epg and result.index("bridge-domain 500") < epg
    assert "segment-member mac-address 0200-fc00-0001 bridge-domain 500" in result


def test_deleting_the_segments_removes_their_epgs_before_their_instances():
    result = lines(patch(AFTER, EMPTY))
    assert result.index("undo traffic-segment segment-id 1") < result.index("undo ip vpn-instance web-500")
    assert result.index("undo traffic-segment enable") < result.index("undo bridge-domain 500")


def test_turning_it_off_removes_the_policy_first_and_epgs_by_id():
    result = lines(patch(AFTER, BEFORE))
    assert result == [
        "undo traffic-segment default-policy permit",
        "undo segment-policy FABRIC",
        "undo segment policy FABRIC",
        "undo segment classifier GBP-1-2",
        "undo segment classifier GBP-4-3",
        "undo segment behavior GBP-1-2",
        "undo segment behavior GBP-4-3",
        "undo traffic-segment segment-id 1",   # by ID, not the whole line
        "undo traffic-segment segment-id 2",
        "undo traffic-segment segment-id 3",
        "undo traffic-segment segment-id 4",
        "undo traffic-segment enable",
    ]


def test_learn_to_enforce_swaps_the_default_policy():
    # deny is the switch's default: typed, never displayed, so not sent (owner, 2026-11-21)
    result = lines(patch(AFTER, AFTER.replace("traffic-segment default-policy permit\n", "traffic-segment default-policy deny\n")))
    assert result == ["undo traffic-segment default-policy permit"], result
    enforce = AFTER.replace("traffic-segment default-policy permit\n", "traffic-segment default-policy deny\n")
    assert lines(patch(enforce, AFTER)) == ["undo traffic-segment default-policy deny", "traffic-segment default-policy permit"]


def test_a_contract_leaves_the_policy_before_its_classifier_and_behavior_go():
    after = AFTER.replace("  classifier GBP-4-3 behavior GBP-4-3 precedence 2\n", "")
    after = after.replace("segment behavior GBP-4-3\n  statistics enable\n", "")
    start, end = after.index("segment classifier GBP-4-3"), after.index("segment policy FABRIC")
    result = lines(patch(AFTER, after[:start] + after[end:]))
    assert result == ["segment policy FABRIC", "undo classifier GBP-4-3 behavior GBP-4-3",
                      "undo segment classifier GBP-4-3", "undo segment behavior GBP-4-3"]


def test_an_epg_changes_in_place_by_its_id():
    result = lines(patch(AFTER, AFTER.replace("web-admins intra-epg-behavior deny", "web-admins intra-epg-behavior permit")))
    assert result[0] == "traffic-segment segment-id 4 segment-name web-admins intra-epg-behavior permit"
    assert not any(line.startswith("undo traffic-segment") for line in result)


def test_a_changed_rule_is_typed_over_in_place():
    # `undo rule 10` is "Incomplete"; typing rule 10 again modifies it
    old = "destination-segment 2 protocol tcp destination-port range 5432 5433"
    result = lines(patch(AFTER, AFTER.replace(old, "destination-segment 2 protocol tcp destination-port eq 5432")))
    assert result == ["segment classifier GBP-1-2",
                      "rule 10 permit ipv4 source-segment 1 destination-segment 2 protocol tcp destination-port eq 5432",
                      "rule 50 permit ipv6 source-segment 1 destination-segment 2 protocol tcp destination-port eq 5432"]


def test_a_removed_rule_is_undone_with_its_whole_line():
    # `undo rule <n> permit ...` as stored
    rule = "rule 80 permit ipv6 source-segment 2 destination-segment 1 protocol 58"
    result = lines(patch(AFTER, AFTER.replace(f"  {rule}\n", "")))
    assert result == ["segment classifier GBP-1-2", f"undo {rule}"]


def test_a_switch_with_the_global_statistics_line_loses_it():
    """Generated until 2026-11-20 (V300R025C10 has no such line); C00 accepted its undo."""
    had = AFTER.replace("traffic-segment default-policy permit\n", "traffic-segment default-policy permit\nsegment-policy statistics enable\n", 1)
    assert had != AFTER
    assert lines(patch(had, AFTER)) == ["undo segment-policy statistics enable"]


def test_an_enforce_switch_without_the_deny_line_is_in_sync():
    enforce = AFTER.replace("traffic-segment default-policy permit\n", "traffic-segment default-policy deny\n")
    assert lines(patch(AFTER.replace("traffic-segment default-policy permit\n", ""), enforce)) == []
