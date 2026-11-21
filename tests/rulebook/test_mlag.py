"""M-LAG patches with our rulebook: DFS key presence, ordering, add and remove."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

RUNNING_DFS = """\
dfs-group 1
 authentication-mode hmac-sha256 password %^%#FAKE-CIPHER%^%#
 dual-active detection source ip 10.10.0.2 peer 10.10.0.3
 priority 150
"""
GENERATED_DFS = RUNNING_DFS.replace("%^%#FAKE-CIPHER%^%#", "dfs-test-key")


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def test_encrypted_dfs_key_matches_plain_key():
    assert patch(RUNNING_DFS, GENERATED_DFS) == ""


def test_missing_dfs_key_is_added():
    without_key = "".join(l for l in RUNNING_DFS.splitlines(keepends=True) if "authentication-mode" not in l)

    assert patch(without_key, GENERATED_DFS).splitlines() == [
        "dfs-group 1",
        " authentication-mode hmac-sha256 password dfs-test-key",
        " quit",
    ]


def test_other_dfs_settings_are_still_compared():
    lines = patch(RUNNING_DFS, GENERATED_DFS.replace("priority 150", "priority 100")).splitlines()

    assert " priority 100" in lines


def test_add_order_dfs_group_then_v_stp_then_bindings():
    new = "stp v-stp enable\n" + GENERATED_DFS + "interface Eth-Trunk1\n mode lacp-static\n dfs-group 1 m-lag 1\n"

    lines = patch("", new).splitlines()

    assert lines.index("dfs-group 1") < lines.index("stp v-stp enable") < lines.index("interface Eth-Trunk1")


def test_remove_dfs_group_after_bindings():
    old = "stp v-stp enable\n" + RUNNING_DFS + "interface Eth-Trunk1\n mode lacp-static\n dfs-group 1 m-lag 1\n"

    lines = patch(old, "").splitlines()

    assert lines.index("undo interface Eth-Trunk1") < lines.index("undo dfs-group 1") < lines.index("undo stp v-stp enable")
