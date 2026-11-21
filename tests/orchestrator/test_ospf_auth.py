"""OSPF authentication: turning it on only with no link up, generated keys that never show,
and a roll whose steps wait for the switches."""

import asyncio
import json
import os

import pytest

from generators.ospf_auth import OspfKeychain
from orchestrator import db, ospf_auth, redact
from orchestrator.deploys import Verifier, VerifySettings
from orchestrator.segments import SegmentError
from tests.orchestrator.test_deploys import FakePrometheus, two_active_leaves


def run(coro):
    return asyncio.run(coro)


class Reader:
    def __init__(self):
        self.keys = {}   # device -> (ids, send)

    async def get_state(self, device, _filter):
        ids, send = self.keys.get(device, ((), None))
        rows = "".join(f"<abs-key-id><key-id>{i}</key-id><default-send-key-id>{str(i == send).lower()}"
                       "</default-send-key-id></abs-key-id>" for i in ids)
        return f'<data><keychain xmlns="{ospf_auth.NS}"><keychains><keychain><abs-key-ids>{rows}</abs-key-ids></keychain></keychains></keychain></data>'


@pytest.fixture
def env(database_url, monkeypatch):
    monkeypatch.setenv("OSPF_KEYS", "{}")
    monkeypatch.setattr(ospf_auth, "KEYS", {})
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    return sessions, two_active_leaves()


def verifier(nb, annet=None, **links):
    return Verifier(annet, FakePrometheus(links={k.replace("_", "-"): v for k, v in links.items()}), nb, VerifySettings())


def test_it_turns_on_only_with_no_link_up_and_never_shows_a_key(env):
    sessions, nb = env
    calm = verifier(nb)
    state = run(ospf_auth.set_enabled(nb, sessions, calm, "example", True, "alice"))
    assert state == {"enabled": True, "keys": [1], "send": 1, "stage": "on"}
    key = json.loads(os.environ["OSPF_KEYS"])["example"]["1"]
    assert len(key) == 48 and key not in str(state) and redact.redact(f"key {key}") == "key ******"
    context = run(nb.find_one("extras/config-contexts", name="fabric-example"))
    assert key not in str(context)


def test_a_link_up_or_a_switch_not_polled_refuses(env, monkeypatch):
    sessions, nb = env
    check = verifier(nb)

    async def ports(_device):
        return ["100GE1/0/1"]
    monkeypatch.setattr(check, "fabric_ports", ports)
    check.prometheus.links = {"leaf1": {"100GE1/0/1": True}}
    with pytest.raises(SegmentError, match="leaf1 100GE1/0/1"):
        run(ospf_auth.set_enabled(nb, sessions, check, "example", True, "alice"))

    async def silent(_device):
        return None, False, {}
    monkeypatch.setattr(check.prometheus, "device_state", silent)
    with pytest.raises(SegmentError, match="not polled"):
        run(ospf_auth.set_enabled(nb, sessions, check, "example", True, "alice"))
    assert run(ospf_auth.get(nb, "example"))["enabled"] is False


def test_a_roll_waits_for_the_switches_at_each_step(env):
    sessions, nb = env
    run(ospf_auth.set_enabled(nb, sessions, verifier(nb), "example", True, "alice"))
    reader = Reader()
    both = ("leaf1", "leaf2")
    roll = lambda action: run(ospf_auth.roll(nb, sessions, reader, "example", action, "alice"))   # noqa: E731
    with pytest.raises(SegmentError, match="cannot send"):
        roll("send")
    assert roll("add")["stage"] == "added"
    assert sorted(ospf_auth.KEYS["example"]) == ["1", "2"]
    reader.keys = {"leaf1": ((1, 2), 1)}
    with pytest.raises(SegmentError, match="not on leaf2"):
        roll("send")
    reader.keys = {d: ((1, 2), 1) for d in both}
    assert roll("send") == {"enabled": True, "keys": [1, 2], "send": 2, "stage": "moved"}
    with pytest.raises(SegmentError, match="still send with key 1"):
        roll("retire")
    reader.keys = {d: ((1, 2), 2) for d in both}
    assert roll("retire") == {"enabled": True, "keys": [2], "send": 2, "stage": "on"}
    assert sorted(ospf_auth.KEYS["example"]) == ["2"]


def test_the_keys_come_back_at_start(env):
    sessions, nb = env
    run(ospf_auth.set_enabled(nb, sessions, verifier(nb), "example", True, "alice"))
    key = ospf_auth.KEYS["example"]["1"]
    ospf_auth.KEYS.clear()
    run(ospf_auth.load(sessions))
    assert ospf_auth.KEYS["example"]["1"] == key


def test_the_generator_owns_the_keychain_once_the_fabric_has_it():
    class Device:
        config_context = {"fabric": {"ospf_auth": {"site": "example", "enabled": True, "keys": [1], "send": 1}}}
    assert "keychain fabric-ospf ~" in OspfKeychain.acl_huawei(None, Device())
