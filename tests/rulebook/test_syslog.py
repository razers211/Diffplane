"""Patches for the platform's syslog loghost with our rulebook, in the forms leaf1 showed
in retyped in place, undone up to its VPN instance."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

WARNING = "info-center loghost 172.24.10.52 vpn-instance _management_vpn_ source-ip 172.24.0.11 level warning\n"
ERROR = "info-center loghost 172.24.10.52 vpn-instance _management_vpn_ source-ip 172.24.0.11 level error port 5514\n"


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_turning_syslog_on_types_the_line():
    assert lines(patch("", WARNING)) == [WARNING.strip()]


def test_a_new_level_or_port_retypes_the_line_in_place():
    assert lines(patch(WARNING, ERROR)) == [ERROR.strip()]


def test_turning_it_off_undoes_it_up_to_its_vpn_instance():
    assert lines(patch(WARNING, "")) == ["undo info-center loghost 172.24.10.52 vpn-instance _management_vpn_"]
