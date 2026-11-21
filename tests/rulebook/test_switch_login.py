"""Patches for switch login with our rulebook, in the stored forms of leaf1."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

AAA = """\
aaa
 authentication-scheme default
  authentication-mode local
 domain default
  authentication-scheme default
 local-user netconf privilege level 3
"""
ON = """\
radius-server template fab-radius
 radius-server user-name domain-included
 radius-server shared-key cipher %+%#stored%+%#
 radius-server accounting 192.0.2.1 1813 vpn-instance _management_vpn_ weight 80
 radius-server authentication 192.0.2.1 1812 vpn-instance _management_vpn_ weight 80
aaa
 authentication-scheme default
  authentication-mode local
 authentication-scheme fab-radius
  authentication-mode radius local
 accounting-scheme fab-radius
  accounting-mode radius
 domain default
  authentication-scheme default
 domain radius
  authentication-scheme fab-radius
  accounting-scheme fab-radius
  radius-server fab-radius
 local-user netconf privilege level 3
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_the_key_is_compared_by_presence():
    assert lines(patch(ON, ON.replace("%+%#stored%+%#", "plainkey"))) == []


def test_removal_is_what_the_switch_took():
    got = lines(patch(ON, AAA))
    assert "undo radius-server template fab-radius" in got
    for line in ("undo domain radius", "undo authentication-scheme fab-radius", "undo accounting-scheme fab-radius"):
        assert line in got
    assert got.index("undo domain radius") < got.index("undo authentication-scheme fab-radius")
    assert got.index("undo authentication-scheme fab-radius") < got.index("undo radius-server template fab-radius")
    assert not any("local-user" in line or "domain default" in line for line in got)


def test_the_domain_comes_after_what_it_names():
    got = lines(patch(AAA, ON))
    assert got.index("radius-server template fab-radius") < got.index("domain radius")
    assert got.index("authentication-scheme fab-radius") < got.index("domain radius")


ACCOUNT = AAA + """\
 local-user person password irreversible-cipher $1d$hash$
 local-user person privilege level 1
 local-user person service-type ssh
"""


def test_an_account_password_is_never_compared():
    assert lines(patch(ACCOUNT, ACCOUNT.replace("$1d$hash$", "plainfirst"))) == []


def test_an_account_goes_whole_and_others_stay():
    got = lines(patch(ACCOUNT, AAA))
    assert got == ["aaa", "undo local-user person", "quit"]
