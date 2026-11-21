"""Answers to VRP prompts in our deploy rulebook, merged with Annet's Huawei rules."""

import pytest
from annet import rulebook
from annet.annlib.netdev.views.hardware import HardwareView
from annet.rulebook.deploying import match_deploy_rule

from tests.rulebook.patch import MODEL_HW, use_our_rulebook


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def dialogs(*path: str) -> dict[str, str]:
    rules = rulebook.get_rulebook(HardwareView(MODEL_HW, ""))["deploying"]
    rule = match_deploy_rule(rules, path, {})
    return {repr(question): answer.text for question, answer in rule["attrs"]["dialogs"].items()}


def test_dfs_group_removal_is_confirmed():
    # Exact prompt seen on leaf1 (V300R023) on.
    assert dialogs("undo dfs-group 1") == {
        "MakeMessageMatcher('Warning: The DFS-Group will be deleted. Continue? [Y/N]:')": "Y"
    }


def test_annet_dialogs_are_still_inherited():
    assert any("BGP process will be deleted" in q for q in dialogs("undo bgp"))


def test_bgp_vpn_instance_removal_is_confirmed():
    # Exact prompt seen on leaf1 (V300R023) on, for `undo vpn-instance FW` in bgp.
    assert dialogs("bgp 65001", "undo vpn-instance FW") == {
        "MakeMessageMatcher('Warning: The vpn session and address family will be deleted. Continue? [Y/N]:')": "Y"
    }
    assert dialogs("bgp 65001", "undo vpn-instance web-500") == dialogs("bgp 65001", "undo vpn-instance FW")


def test_bgp_vpn_peer_removal_is_confirmed():
    # Exact prompt seen on V300R023: `undo peer` in
    # `bgp 65001` / `vpn-instance FW`.
    assert dialogs("bgp 65001", "vpn-instance FW", "undo peer 10.150.25.5") == {
        "MakeMessageMatcher('Warning: All the configurations related to the BGP peer will be deleted. Continue? [Y/N]:')": "Y"
    }
