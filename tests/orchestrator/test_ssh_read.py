"""Read-only SSH shell for the importer: only listed commands, output without echo and prompt."""

import base64
from types import SimpleNamespace

import pytest

from orchestrator import hostkeys, ssh_read


class FakeChannel:
    """Answers each command with a canned reply, the way a VRP shell echoes and prompts."""

    def __init__(self, replies: dict[str, list[str]]):
        self.replies = replies
        self.pending = ["\r\nInfo: The max number of VTY users is 21.\r\n<leaf1>"]
        self.sent = []

    def recv_ready(self):
        return bool(self.pending)

    def recv(self, size):
        return self.pending.pop(0).encode()

    def exit_status_ready(self):
        return False

    def send(self, text):
        self.sent.append(text)
        if text == " ":
            self.pending += self.replies.pop("<space>")
            return
        command = text.rstrip("\n")
        self.pending += self.replies.get(command, [f"{command}\r\n<leaf1>"])


def test_output_without_echo_prompt_and_paging():
    channel = FakeChannel({
        "display lldp neighbor brief": ["display lldp neighbor brief\r\nLocal Interface  Exptime(s)", " Neighbor\r\n<leaf1>"],
        "display version": ["display version\r\nHuawei YunShan OS\r\n  ---- More ----"],
        "<space>": ["\x1b[42D                \x1b[42DVRP (R) software\r\n<leaf1>"],
    })
    shell = ssh_read.Shell(channel, timeout=5)
    assert shell.start() == "leaf1"
    assert shell.run("screen-length 0 temporary") == "\n"
    assert shell.run("display lldp neighbor brief") == "Local Interface  Exptime(s) Neighbor\n"
    assert shell.run("display version") == "Huawei YunShan OS\nVRP (R) software\n"
    assert channel.sent == ["screen-length 0 temporary\n", "display lldp neighbor brief\n", "display version\n", " "]


def test_only_listed_commands_are_sent():
    shell = ssh_read.Shell(FakeChannel({}), timeout=5)
    for bad in ("system-view", "display current-configuration\nsystem-view", "undo stelnet server enable",
                "display bgp peer"):
        with pytest.raises(ValueError):
            shell.run(bad)
    with pytest.raises(ValueError):
        ssh_read.read("192.0.2.1", ["ssh-rsa AAAA"], "x", "y", ["save"])
    with pytest.raises(ssh_read.SshReadError, match="no approved host key"):
        ssh_read.read("192.0.2.1", [], "x", "y", ["display version"])


def test_a_silent_switch_times_out():
    channel = FakeChannel({})
    channel.pending = []
    with pytest.raises(ssh_read.SshReadError, match="no prompt"):
        ssh_read.Shell(channel, timeout=0.2).start()


REPLY = ('<rpc-reply xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><cli-results xmlns="urn:huawei:yang:huawei-cli">'
         '<cli-result><sequence>1</sequence><sub-sequence>1</sub-sequence><command>display version</command>'
         '<execute-result>success</execute-result><cmd-output-info>V300</cmd-output-info></cli-result>'
         '<cli-result><sequence>2</sequence><sub-sequence>1</sub-sequence>'
         '<command>display current-configuration</command><execute-result>success</execute-result>'
         '<cmd-output-info>sysname leaf-a\n#\n</cmd-output-info></cli-result></cli-results></rpc-reply>')
COMMANDS = ["display version", "display current-configuration"]


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def dispatch(self, rpc):
        return SimpleNamespace(xml=REPLY)


def fake_connect(monkeypatch, connect):
    from ncclient import manager
    monkeypatch.setattr(manager, "connect", connect)


def test_netconf_checks_the_approved_key_and_returns_the_cli_text(monkeypatch):
    key = "ssh-rsa " + base64.b64encode(b"key").decode()

    def connect(**kw):
        assert kw["hostkey_verify"] is True and kw["port"] == 830
        assert kw["unknown_host_cb"]("h", hostkeys.md5_fingerprint(key))
        assert not kw["unknown_host_cb"]("h", "00:11")
        return FakeSession()

    fake_connect(monkeypatch, connect)
    assert ssh_read.netconf_read("192.0.2.1", [key], "x", "y", COMMANDS) == ("leaf-a", ["V300", "sysname leaf-a\n#\n"])


def test_netconf_never_sends_an_unlisted_command(monkeypatch):
    fake_connect(monkeypatch, lambda **kw: FakeSession())
    with pytest.raises(ValueError):
        ssh_read.netconf_read("192.0.2.1", [], "x", "y", ["save"])


def pins():
    return {22: ["ssh-rsa QUFB"], 830: ["ssh-rsa QkJC"]}


def test_ssh_is_the_fallback_when_netconf_does_not_answer(monkeypatch):
    def connect(**kw):
        raise ConnectionRefusedError("port 830")

    fake_connect(monkeypatch, connect)
    monkeypatch.setattr(ssh_read, "read", lambda host, keys, *_: ("over-ssh", list(keys)))
    assert ssh_read.read_switch("192.0.2.1", pins(), "x", "y", COMMANDS) == ("over-ssh", ["ssh-rsa QUFB"])


def test_ssh_alone_when_the_switch_has_no_netconf_key(monkeypatch):
    def connect(**kw):
        raise AssertionError("NETCONF was tried")

    fake_connect(monkeypatch, connect)
    monkeypatch.setattr(ssh_read, "read", lambda host, keys, *_: ("over-ssh", list(keys)))
    assert ssh_read.read_switch("192.0.2.1", {22: ["ssh"]}, "x", "y", COMMANDS)[0] == "over-ssh"


def test_a_wrong_netconf_key_is_refused_never_a_fallback(monkeypatch):
    from ncclient.transport.errors import SSHUnknownHostError

    def connect(**kw):
        raise SSHUnknownHostError("192.0.2.1", "00:11")

    def read(*_):
        raise AssertionError("fell back to SSH")

    fake_connect(monkeypatch, connect)
    monkeypatch.setattr(ssh_read, "read", read)
    with pytest.raises(ssh_read.SshReadError, match="another NETCONF host key"):
        ssh_read.read_switch("192.0.2.1", pins(), "x", "y", COMMANDS)
