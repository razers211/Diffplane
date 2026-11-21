"""Patches for a copper port's fixed speed with our rulebook, in the forms a switch test
showed under commit trial: `negotiation disable` before `speed`, `undo speed` bare."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

AUTO = "interface 10GE1/0/20\n"
FIXED = "interface 10GE1/0/20\n negotiation disable\n speed 1000\n"
SLOWER = "interface 10GE1/0/20\n negotiation disable\n speed 100\n"


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() not in ("quit", "#")]


def test_a_fixed_speed_turns_negotiation_off_first():
    assert lines(patch(AUTO, FIXED)) == ["interface 10GE1/0/20", "negotiation disable", "speed 1000"]


def test_back_to_auto_undoes_the_speed_then_negotiation():
    assert lines(patch(FIXED, AUTO)) == ["interface 10GE1/0/20", "undo speed", "undo negotiation disable"]


def test_another_speed_undoes_the_old_one_first():
    assert lines(patch(FIXED, SLOWER)) == ["interface 10GE1/0/20", "undo speed", "speed 100"]
