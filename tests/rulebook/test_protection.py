"""Patches for edge-port protection with our rulebook, in the undo forms leaf1 offered in
storm control undone by packet type, error-down recovery without its interval."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

ON = """\
stp bpdu-protection
error-down auto-recovery cause bpdu-protection interval 45
error-down auto-recovery cause loopback-detect interval 45
error-down auto-recovery cause storm-control interval 45
interface 10GE1/0/10
 storm control broadcast min-rate percent 1 max-rate percent 1
 storm control multicast min-rate percent 1 max-rate percent 1
 storm control unknown-unicast min-rate percent 5 max-rate percent 5
 storm control action block
 storm control enable log
 storm control enable trap
 stp edged-port enable
 loopback-detect enable
 loopback-detect action error-down
 loopback-detect recovery-time 45
"""
WATCH = """\
interface 10GE1/0/10
 storm control broadcast min-rate percent 1 max-rate percent 1
 storm control multicast min-rate percent 1 max-rate percent 1
 storm control unknown-unicast min-rate percent 5 max-rate percent 5
 storm control enable log
 storm control enable trap
 stp edged-port enable
 loopback-detect enable
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_turning_it_on_types_every_line():   # in Annet's order: the port's block first (no dependency)
    assert sorted(lines(patch("", ON))) == sorted(lines(ON) + ["quit"])


def test_turning_it_off_undoes_each_line_in_the_offered_form():
    assert sorted(lines(patch(ON, ""))) == sorted([
        "undo stp bpdu-protection",
        "undo error-down auto-recovery cause bpdu-protection",
        "undo error-down auto-recovery cause loopback-detect",
        "undo error-down auto-recovery cause storm-control",
        "interface 10GE1/0/10",
        "undo storm control broadcast",
        "undo storm control multicast",
        "undo storm control unknown-unicast",
        "undo storm control action",
        "undo storm control enable log",
        "undo storm control enable trap",
        "undo stp edged-port",
        "undo loopback-detect enable",
        "undo loopback-detect action error-down",
        "undo loopback-detect recovery-time 45",
        "quit",
    ])


def test_on_to_watch_drops_the_actions_and_the_globals():
    assert sorted(lines(patch(ON, WATCH))) == sorted([
        "undo stp bpdu-protection",
        "undo error-down auto-recovery cause bpdu-protection",
        "undo error-down auto-recovery cause loopback-detect",
        "undo error-down auto-recovery cause storm-control",
        "interface 10GE1/0/10",
        "undo storm control action",
        "undo loopback-detect action error-down",
        "undo loopback-detect recovery-time 45",
        "quit",
    ])


def test_a_new_recovery_time_is_retyped_in_place():
    old = "error-down auto-recovery cause storm-control interval 45\n"
    assert lines(patch(old, old.replace("45", "60"))) == ["error-down auto-recovery cause storm-control interval 60"]
