import pytest

from orchestrator import redact, switch_cli, switch_login
from orchestrator.segments import SegmentError


def test_domain_cannot_be_the_platforms():
    for bad in ("default", "default_admin", "Radius", "a b", ""):
        with pytest.raises(SegmentError):
            switch_login._checked(bad, ["192.0.2.1"], "12345678")


def test_servers_and_key_are_checked():
    switch_login._checked("radius", ["192.0.2.1", "192.0.2.2"], None)
    for servers, key in ((["nope"], None), ([], None), (["192.0.2.1"] * 2, None), (["192.0.2.1"], "short"),
                         (["192.0.2.1"], "with a space")):
        with pytest.raises(SegmentError):
            switch_login._checked("radius", servers, key)


def test_a_key_is_masked_once_known():
    switch_login.remember("t", "supersecretkey")
    assert "supersecretkey" not in redact.redact("radius-server shared-key cipher supersecretkey")


def test_trial_confirms_only_after_the_check():
    async def check():
        return None
    steps = switch_cli.trial_steps("aaa\n  domain radius\n  quit\n", check, ("k",))
    words = [s.command if not callable(s) else "CHECK" for s in steps]
    assert words == ["system-view", "aaa", "domain radius", "quit", "commit trial 300", "CHECK", "commit", "return"]


def test_the_generators_acl_never_names_a_default_or_another_account():
    from generators.switch_login import SwitchLogin

    class Device:
        config_context = {"fabric": {"local_accounts": {"site": "t", "accounts": {}, "owned": ["person"]}}}
    acl = SwitchLogin.acl_huawei(None, Device())
    assert "local-user person ~" in acl and "domain default" not in acl and "local-user ~" not in acl


@pytest.mark.parametrize("name,level,password,word", [
    ("short", 1, "password1", "6 to 32"), ("netconf", 3, "password1", "platform"), ("person1", 2, "password1", "level"),
    ("person1", 1, "has space1", "password"),
])
def test_account_checks(monkeypatch, name, level, password, word):
    import asyncio
    monkeypatch.setenv("GNETCLI_DEV_LOGIN", "netconf")
    with pytest.raises(SegmentError, match=word):
        asyncio.run(switch_login.add_account(None, None, None, "t", name, level, password, "u"))
