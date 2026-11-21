"""Patches for the Snmp generator's lines, in both directions, with our rulebook.

LAB_RUNNING is the SNMP part of leaf1's running config after the first deploy,
with the encrypted community replaced by a fake of the same shape.
"""

import pytest

from rulebook.huawei.snmp import community_key
from tests.rulebook.patch import patch, use_our_rulebook

CIPHER = '%@%##!!!!!!!!!"!!!!#!!!!*!!!!FAKEFAKEFAKE\\-6U"(qzo!!!!!!!!!!%@%#'

LAB_RUNNING = f"""\
snmp-agent
snmp-agent community read cipher {CIPHER} alias __CommunityAliasName_01_46652
snmp-agent sys-info contact noc@example.com
snmp-agent sys-info location example
snmp-agent sys-info version v2c
snmp-agent sys-info version v3 disable
"""


def generated(alias: str = "ro-1234abcd") -> str:
    return f"""\
snmp-agent
snmp-agent community read ANNET-TEST alias {alias}
snmp-agent sys-info contact noc@example.com
snmp-agent sys-info location example
snmp-agent sys-info version v2c
snmp-agent sys-info version v3 disable
"""


def running_with_alias(alias: str) -> str:
    return LAB_RUNNING.replace("__CommunityAliasName_01_46652", alias)


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def test_encrypted_community_with_our_alias_is_unchanged():
    assert patch(running_with_alias("ro-1234abcd"), generated("ro-1234abcd")) == ""


def test_changed_community_is_replaced():
    result = patch(running_with_alias("ro-1234abcd"), generated("ro-99999999"))

    assert result.splitlines() == [
        f"undo snmp-agent community read cipher {CIPHER}",
        "snmp-agent community read ANNET-TEST alias ro-99999999",
    ]


def test_community_without_our_alias_is_replaced():
    # The example switch today: the community was first set without an alias, VRP named it.
    result = patch(LAB_RUNNING, generated())

    assert result.splitlines() == [
        f"undo snmp-agent community read cipher {CIPHER}",
        "snmp-agent community read ANNET-TEST alias ro-1234abcd",
    ]


def test_add_to_switch_with_default_snmp():
    result = patch("snmp-agent\nsnmp-agent sys-info version v3\n", generated())

    assert result.splitlines() == [
        "snmp-agent sys-info contact noc@example.com",
        "snmp-agent sys-info location example",
        "snmp-agent sys-info version v2c",
        "snmp-agent community read ANNET-TEST alias ro-1234abcd",
        "snmp-agent sys-info version v3 disable",
    ]


def test_remove_community():
    result = patch(running_with_alias("ro-1234abcd"), generated().replace(
        "snmp-agent community read ANNET-TEST alias ro-1234abcd\n", ""))

    assert result.splitlines() == [f"undo snmp-agent community read cipher {CIPHER}"]


def test_version_lines_as_displayed_need_no_patch():
    assert patch(LAB_RUNNING, LAB_RUNNING) == ""


@pytest.mark.parametrize(
    "row, key",
    [
        (f"snmp-agent community read cipher {CIPHER} alias ro-1", ("read", "ro-1")),
        ("snmp-agent community read ANNET-TEST alias ro-1", ("read", "ro-1")),
        ("snmp-agent community write X alias w", ("write", "w")),
        ("snmp-agent community read ANNET-TEST", None),
        ("snmp-agent community read X alias", None),
        ("snmp-agent sys-info version v2c", None),
    ],
)
def test_community_key(row, key):
    assert community_key(row) == key


# SNMPv3, in the forms leaf1 stored in. The password lines are the
# password job's: the generator never writes them, so a running config's are filtered out by its
# ACL before the diff; here the running config is written without them.
V3_RUNNING = f"""\
snmp-agent
snmp-agent community read cipher {CIPHER} alias ro-1234abcd
snmp-agent sys-info version v2c v3
snmp-agent protocol source-interface MEth0/0/0
snmp-agent protocol vpn-instance _management_vpn_
snmp-agent group v3 FABRIC-RO privacy notify-view ViewDefault
snmp-agent target-host host-name NMS-1 trap address udp-domain 192.0.2.10 vpn-instance _management_vpn_ params securityname nms v3 privacy
snmp-agent usm-user v3 nms
snmp-agent usm-user v3 nms group FABRIC-RO
"""
V2C_RUNNING = f"""\
snmp-agent
snmp-agent community read cipher {CIPHER} alias ro-1234abcd
snmp-agent sys-info version v2c
snmp-agent sys-info version v3 disable
"""


def generated_v3() -> str:
    return V3_RUNNING.replace(f"cipher {CIPHER}", "ANNET-TEST")


def test_adding_a_reader_and_its_trap_target():
    assert lines(patch(V2C_RUNNING, generated_v3().replace("ANNET-TEST", f"cipher {CIPHER}"))) == [
        "snmp-agent sys-info version v3",   # stored as `version v2c v3`
        "snmp-agent group v3 FABRIC-RO privacy notify-view ViewDefault",
        "snmp-agent usm-user v3 nms",
        "snmp-agent usm-user v3 nms group FABRIC-RO",
        "snmp-agent target-host host-name NMS-1 trap address udp-domain 192.0.2.10 vpn-instance _management_vpn_ "
        "params securityname nms v3 privacy",
        "snmp-agent protocol source-interface MEth0/0/0",
        "snmp-agent protocol vpn-instance _management_vpn_",
    ]


def test_the_same_reader_is_no_change():
    assert patch(V3_RUNNING, V3_RUNNING) == ""


def test_removing_the_reader_undoes_target_user_and_group_in_short_forms():
    assert lines(patch(V3_RUNNING, V2C_RUNNING)) == [
        "undo snmp-agent protocol source-interface MEth0/0/0",   # the listener goes first
        "undo snmp-agent protocol vpn-instance",
        "undo snmp-agent target-host host-name NMS-1",
        "undo snmp-agent usm-user v3 nms",
        "undo snmp-agent group v3 FABRIC-RO privacy",
        "snmp-agent sys-info version v3 disable",   # once nothing uses v3
    ]


def test_a_moved_trap_target_is_retyped_under_its_name():
    moved = V3_RUNNING.replace("192.0.2.10", "192.0.2.20")
    assert lines(patch(V3_RUNNING, moved)) == [next(x for x in moved.splitlines() if "target-host" in x)]


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]



def test_v3_alone_turns_v2c_off_without_all_disable():
    """`version all disable` is refused while a target host uses v3."""
    v3 = V3_RUNNING.replace("snmp-agent sys-info version v2c v3", "snmp-agent sys-info version v3")
    v3 = "\n".join(line for line in v3.splitlines() if not line.startswith("snmp-agent community"))
    assert lines(patch(V3_RUNNING, v3)) == [f"undo snmp-agent community read cipher {CIPHER}",
                                            "snmp-agent sys-info version v2c disable"]
    assert lines(patch(v3, V3_RUNNING))[0] == "snmp-agent sys-info version v2c"
