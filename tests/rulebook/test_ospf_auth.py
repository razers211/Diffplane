"""Patches for OSPF authentication with our rulebook, in the forms leaf1 took in console
rounds 1-3 (candidate only): adding, moving the send key, retiring a key, turning it off."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

ONE = """\
keychain fabric-ospf mode absolute
 key-id 1
  algorithm hmac-sha-256
  key-string cipher %+%#stored1%+%#
  receive-time 00:00 2020-01-01 duration infinite
  default send-key-id
interface 100GE1/0/1
 ospf authentication-mode keychain fabric-ospf
"""
KEY_2 = """\
 key-id 2
  algorithm hmac-sha-256
  key-string cipher plain2
  receive-time 00:00 2020-01-01 duration infinite
"""
NO_LINK = "interface 100GE1/0/1\n"


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def two(send: int) -> str:
    head, tail = ONE.split("interface")
    if send == 2:
        return head.replace("  default send-key-id\n", "") + KEY_2 + "  default send-key-id\ninterface" + tail
    return head + KEY_2 + "interface" + tail


def test_the_same_key_generated_in_plain_is_no_change():
    assert patch(ONE, ONE.replace("%+%#stored1%+%#", "plain1")) == ""


def test_turning_it_on_makes_the_keychain_before_the_interface_names_it():
    assert lines(patch(NO_LINK, ONE)) == [
        "keychain fabric-ospf mode absolute", "key-id 1", "algorithm hmac-sha-256", "key-string cipher %+%#stored1%+%#",
        "receive-time 00:00 2020-01-01 duration infinite", "default send-key-id", "quit", "quit",
        "interface 100GE1/0/1", "ospf authentication-mode keychain fabric-ospf", "quit",
    ]


def test_a_roll_adds_moves_and_retires():
    assert "key-string cipher plain2" in lines(patch(ONE, two(1)))
    moved = lines(patch(two(1).replace("plain2", "%+%#stored2%+%#"), two(2)))
    assert moved.index("undo default send-key-id") < moved.index("default send-key-id")
    assert not [line for line in moved if "key-string" in line]
    retired = two(2).replace("plain2", "%+%#stored2%+%#")
    new = retired.replace("""\
 key-id 1
  algorithm hmac-sha-256
  key-string cipher %+%#stored1%+%#
  receive-time 00:00 2020-01-01 duration infinite
""", "")
    assert lines(patch(retired, new)) == ["keychain fabric-ospf mode absolute", "undo key-id 1", "quit"]


def test_turning_it_off_leaves_the_interface_first_and_undoes_in_short_forms():
    assert lines(patch(ONE, NO_LINK)) == [
        "interface 100GE1/0/1", "undo ospf authentication-mode", "quit", "undo keychain fabric-ospf",
    ]
