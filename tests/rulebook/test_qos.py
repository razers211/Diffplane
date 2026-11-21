"""Patches for QoS, both directions, with our rulebook, in the forms leaf1 accepted
(V300R025-5): the mark's classifier, behavior and policy before the ports and
bridge domains using them and removed after them; `undo trust`; weights undone without a value."""

from pathlib import Path

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

GOLDEN = Path(__file__).parents[1] / "golden"
BEFORE = (GOLDEN / "example-segments" / "leaf1.cfg").read_text()   # segments, QoS off
AFTER = (GOLDEN / "example-qos" / "leaf1.cfg").read_text()         # the same with QoS on and two marks
EMPTY = (GOLDEN / "example" / "leaf1.cfg").read_text()             # no segments at all


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "quit"]


def block(result: list[str], header: str) -> list[str]:
    start = result.index(header) + 1
    end = next((i for i in range(start, len(result)) if result[i].startswith(("interface ", "bridge-domain ", "undo "))
                and not result[i].startswith(("undo qos", "undo trust", "undo traffic-policy"))), len(result))
    return result[start:end]


def test_turning_it_on_makes_the_policies_before_their_users():
    result = lines(patch(BEFORE, AFTER))
    policy = result.index("traffic policy QOS-STORAGE")
    assert result.index("traffic classifier QOS-ANY type or") < result.index("traffic behavior QOS-STORAGE") < policy
    assert policy < result.index("bridge-domain 500") < result.index("traffic-policy QOS-STORAGE inbound")
    assert block(result, "interface 10GE1/0/3") == [
        "qos drr 0 to 4", *(f"qos queue {q} drr weight {w}" for q, w in ((0, 30), (1, 5), (2, 15), (3, 35), (4, 15))),
        "trust dscp", "traffic-policy QOS-SCAVENGER inbound"]
    assert block(result, "interface Eth-Trunk1") == ["trust dscp"]
    assert "trust dscp" not in block(result, "interface 10GE1/0/1")      # a trunk member: schedule only
    assert "trust dscp" not in block(result, "interface 100GE1/0/1")     # a routed uplink: schedule only


def test_turning_it_off_undoes_trust_and_weights_in_the_accepted_forms():
    result = lines(patch(AFTER, BEFORE))
    assert "undo trust dscp" not in result and "undo trust" in result
    port = block(result, "interface 10GE1/0/3")
    assert sorted(port) == sorted([*(f"undo qos queue {q} drr weight" for q in range(5)), "undo qos drr 0 to 4",
                                   "undo trust", "undo traffic-policy QOS-SCAVENGER inbound"])
    assert port.index("undo qos queue 4 drr weight") < port.index("undo qos drr 0 to 4")   # as accepted
    assert result[-5:] == ["undo traffic policy QOS-STORAGE", "undo traffic policy QOS-SCAVENGER",
                           "undo traffic behavior QOS-STORAGE", "undo traffic behavior QOS-SCAVENGER",
                           "undo traffic classifier QOS-ANY"]
    assert result.index("undo traffic-policy QOS-STORAGE inbound") < result.index("undo traffic policy QOS-STORAGE")


def test_deleting_a_marked_segment_removes_its_bridge_domain_before_the_policy():
    result = lines(patch(AFTER, EMPTY))
    assert result.index("undo bridge-domain 500") < result.index("undo traffic policy QOS-STORAGE")


def test_a_new_weight_is_retyped_in_place():
    changed = AFTER.replace("qos queue 3 drr weight 35", "qos queue 3 drr weight 40")
    result = lines(patch(AFTER, changed))
    assert result.count("qos queue 3 drr weight 40") == AFTER.count("qos queue 3 drr weight 35")
    assert not [line for line in result if line.startswith("undo")]
