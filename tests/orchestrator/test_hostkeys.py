"""SSH host-key pinning: pins in NetBox, known_hosts for gnetcli, the pin command."""

import asyncio
import base64
import struct

import paramiko
import pytest

from orchestrator import hostkeys
from orchestrator.annet import AnnetError
from tests.collector.test_collector import example_netbox


def rsa_key() -> str:
    key = paramiko.RSAKey.generate(2048)
    return f"{key.get_name()} {key.get_base64()}"


def ecdsa_key() -> tuple[str, str]:
    key = paramiko.ECDSAKey.generate()
    return f"{key.get_name()} {key.get_base64()}", key.fingerprint


def run(coro):
    return asyncio.run(coro)


def device(nb, name="leaf1") -> dict:
    return run(nb.find_one("dcim/devices", name=name))


def pin(nb, name, keys22, keys830=None):
    found = device(nb, name)
    run(nb.update("dcim/devices", found["id"], {"custom_fields": {hostkeys.FIELD: {
        "22": keys22, "830": keys22 if keys830 is None else keys830}}}))


def test_fingerprint_matches_paramiko():
    key, expected = ecdsa_key()
    assert hostkeys.fingerprint(key) == expected


def test_parse_key_normalises_and_rejects_mismatches():
    key, _ = ecdsa_key()
    assert hostkeys.parse_key(key + " comment") == key
    with pytest.raises(hostkeys.HostKeyError, match="does not match"):
        hostkeys.parse_key("ssh-ed25519 " + key.split()[1])
    with pytest.raises(hostkeys.HostKeyError):
        hostkeys.parse_key("ssh-rsa not-base64!")
    with pytest.raises(hostkeys.HostKeyError):
        hostkeys.parse_key("only-one-word")
    blob = base64.b64encode(struct.pack(">I", 7) + b"ssh-rsa" + b"\0" * 8).decode()
    assert hostkeys.parse_key(f"ssh-rsa {blob}") == f"ssh-rsa {blob}"


def test_known_hosts_lines_per_port_only_for_pinned_devices():
    nb = example_netbox()
    key, _ = ecdsa_key()
    other, _ = ecdsa_key()
    pin(nb, "leaf1", [key], [other])
    pin(nb, "leaf2", [key])   # planned, no management address: never listed

    devices = run(nb.find("dcim/devices"))
    assert hostkeys.known_hosts_lines(devices) == [f"172.24.0.11 {key}", f"[172.24.0.11]:830 {other}"]


def test_netconf_check_accepts_only_the_pinned_netconf_keys():
    """ncclient passes paramiko's MD5 fingerprint (get_fingerprint) to the callback."""
    nb = example_netbox()
    pinned = paramiko.RSAKey.generate(2048)
    other = paramiko.RSAKey.generate(2048)
    pin(nb, "leaf1", [rsa_key()], [f"ssh-rsa {pinned.get_base64()}"])

    def colon(key):
        return ":".join(f"{b:02x}" for b in key.get_fingerprint())

    check = hostkeys.netconf_check(device(nb))
    assert check("172.24.0.11", colon(pinned)) is True
    assert check("172.24.0.11", colon(other)) is False
    pin(nb, "leaf1", [rsa_key()], [])
    assert hostkeys.netconf_check(device(nb)) is None


def test_guard_refuses_unpinned_and_writes_known_hosts(tmp_path):
    nb = example_netbox()
    path = tmp_path / "known_hosts"
    guard = hostkeys.Guard(nb, str(path))

    with pytest.raises(AnnetError, match="no pinned SSH host key"):
        run(guard("leaf1"))
    assert not path.exists()

    key, _ = ecdsa_key()
    pin(nb, "leaf1", [key])
    run(guard("leaf1"))
    assert path.read_text() == f"172.24.0.11 {key}\n[172.24.0.11]:830 {key}\n"


def test_guard_refuses_when_known_hosts_is_not_configured():
    with pytest.raises(AnnetError, match="KNOWN_HOSTS_FILE"):
        run(hostkeys.Guard(example_netbox(), "")("leaf1"))


def test_guard_turns_netbox_errors_into_annet_errors(tmp_path):
    from orchestrator.netbox import NetBoxError

    class Down:
        async def find_one(self, *_args, **_kwargs):
            raise NetBoxError("cannot reach NetBox")

    with pytest.raises(AnnetError, match="cannot reach NetBox"):
        run(hostkeys.Guard(Down(), str(tmp_path / "kh"))("leaf1"))


def test_plan_pin_requires_keys_on_both_ports_and_given_keys_to_cover_the_switch():
    a, _ = ecdsa_key()
    b = rsa_key()
    with pytest.raises(hostkeys.HostKeyError, match="port 830"):
        hostkeys.plan_pin({22: [a], 830: []}, [])
    assert hostkeys.plan_pin({22: [b, a], 830: [a]}, []) == {22: sorted([a, b]), 830: [a]}
    with pytest.raises(hostkeys.HostKeyError, match="did not give"):
        hostkeys.plan_pin({22: [a, b], 830: [a]}, [a])
    assert hostkeys.plan_pin({22: [a], 830: [a]}, [a]) == {22: [a], 830: [a]}


def command(nb, action, keys=(), answer="yes", fetched=None):
    audits = []

    async def audit(action, detail):
        audits.append((action, detail))

    code = run(hostkeys.command(nb, action, "leaf1", list(keys), confirm=lambda _prompt: answer,
                                fetch=lambda host, port: hostkeys.Offer((fetched or {})[port]), audit=audit))
    return code, audits


def test_pin_writes_netbox_only_after_yes_and_is_audited():
    nb = example_netbox()
    key, _ = ecdsa_key()
    fetched = {22: [key], 830: [key]}

    code, audits = command(nb, "pin", answer="no", fetched=fetched)
    assert code == 1 and audits == [] and hostkeys.pins(device(nb)) == {22: [], 830: []}

    code, audits = command(nb, "pin", fetched=fetched)
    assert code == 0 and hostkeys.pins(device(nb)) == {22: [key], 830: [key]}
    assert audits == [("hostkey.pin", f"leaf1 172.24.0.11 {hostkeys.fingerprint(key)}")]


def test_a_replaced_switch_needs_a_new_pin():
    """Same name, same IP, new hardware: the key differs, so it is shown next to the old pin."""
    nb = example_netbox()
    old, _ = ecdsa_key()
    new, _ = ecdsa_key()
    pin(nb, "leaf1", [old])

    code, _ = command(nb, "pin", answer="no", fetched={22: [new], 830: [new]})
    assert code == 1 and hostkeys.pins(device(nb))[22] == [old]
    code, _ = command(nb, "pin", fetched={22: [new], 830: [new]})
    assert code == 0 and hostkeys.pins(device(nb))[22] == [new]


def test_forget_removes_the_pin():
    nb = example_netbox()
    key, _ = ecdsa_key()
    pin(nb, "leaf1", [key])

    code, audits = command(nb, "forget")
    assert code == 0 and hostkeys.pins(device(nb)) == {22: [], 830: []}
    assert audits == [("hostkey.forget", "leaf1")]


def test_runner_checks_the_pin_before_connecting(tmp_path):
    from tests.orchestrator.test_device_config import runner

    calls = []

    async def before_connect(device):
        calls.append(device)
        if device == "unpinned":
            raise AnnetError("unpinned has no pinned SSH host key")

    annet = runner(tmp_path)
    annet.before_connect = before_connect

    async def every():   # one event loop: the runner's workers belong to it
        try:
            await annet.gen("leaf1")
            await annet.diff("leaf1")
            await annet.patch("leaf1")
            await annet.deploy("leaf1")
            with pytest.raises(AnnetError, match="no pinned"):
                await annet.diff("unpinned")
        finally:
            await annet.close()

    run(every())
    assert calls == ["leaf1", "leaf1", "leaf1", "unpinned"]   # not for gen: it connects to nothing


def kexinit(host_key_algorithms: str) -> bytes:
    def name_list(text: str) -> bytes:
        return struct.pack(">I", len(text)) + text.encode()

    return (bytes([20]) + b"\x00" * 16 + name_list("curve25519-sha256,diffie-hellman-group14-sha256")
            + name_list(host_key_algorithms) + name_list("aes128-ctr") * 2 + name_list("hmac-sha2-256") * 2
            + name_list("none") * 2 + name_list("") * 2 + b"\x00" + b"\x00" * 4)


def test_parse_kexinit_lists_the_host_key_algorithms():
    assert hostkeys.parse_kexinit(kexinit("rsa-sha2-512,rsa-sha2-256,ssh-rsa,ecdsa-sha2-nistp256")) == [
        "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa", "ecdsa-sha2-nistp256"]
    with pytest.raises(hostkeys.HostKeyError):
        hostkeys.parse_kexinit(b"\x15")


def test_fetch_asks_once_per_key_and_retries_a_reset(monkeypatch):
    calls = []
    resets = ["rsa-sha2-512"]

    def fetch_one(host, port, algorithm, timeout):
        calls.append(algorithm)
        if algorithm in resets:
            resets.remove(algorithm)
            raise ConnectionResetError("reset by peer")
        return f"{hostkeys.FETCHABLE[algorithm]} AAAA{algorithm}"

    monkeypatch.setattr(hostkeys, "offered_algorithms",
                        lambda host, port, timeout: ["rsa-sha2-512", "rsa-sha2-256", "ssh-rsa", "ecdsa-sha2-nistp256",
                                                     "x509v3-sign-rsa"])
    monkeypatch.setattr(hostkeys, "_fetch_one", fetch_one)
    monkeypatch.setattr(hostkeys, "CONNECT_PAUSE", 0)
    monkeypatch.setattr(hostkeys, "RETRY_PAUSE", 0)

    offer = hostkeys.fetch_keys("172.24.0.11", 830)

    assert calls == ["rsa-sha2-512", "rsa-sha2-512", "ecdsa-sha2-nistp256"]   # RSA once (plus its retry)
    assert offer.keys == ["ecdsa-sha2-nistp256 AAAAecdsa-sha2-nistp256", "ssh-rsa AAAArsa-sha2-512"]
    assert offer.unsupported == ["x509v3-sign-rsa"]


def test_fetch_gives_up_after_one_retry(monkeypatch):
    def reset(*_args):
        raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(hostkeys, "offered_algorithms", reset)
    monkeypatch.setattr(hostkeys, "RETRY_PAUSE", 0)
    with pytest.raises(hostkeys.HostKeyError, match="reset by peer"):
        hostkeys.fetch_keys("172.24.0.11", 830)


# -- pinning from the GUI ------------------------------------------------------------------

def offering(*keys):
    return lambda host, port: hostkeys.Offer(list(keys), ["rsa-sha2-512"])


def test_offer_shows_fingerprints_next_to_the_pins():
    nb = example_netbox()
    key = rsa_key()
    shown = run(hostkeys.offer(nb, "leaf1", fetch=offering(key)))
    assert shown["address"] == "172.24.0.11" and shown["unchanged"] is False and shown["same_on_both_ports"]
    (info,) = shown["offered"]["22"]
    assert info == {"key": key, "type": "ssh-rsa", "sha256": hostkeys.fingerprint(key), "md5": hostkeys.md5_fingerprint(key)}
    assert shown["pinned"] == {"22": [], "830": []}
    with pytest.raises(hostkeys.HostKeyError, match="activate it first"):
        run(hostkeys.offer(nb, "leaf2", fetch=offering(key)))


def test_pin_only_what_was_approved_and_is_still_offered():
    nb = example_netbox()
    key, other = rsa_key(), rsa_key()
    with pytest.raises(hostkeys.HostKeyError, match="did not give"):          # the switch offers another key
        run(hostkeys.pin_approved(nb, "leaf1", [key], fetch=offering(other)))
    with pytest.raises(hostkeys.HostKeyError, match="no longer offers"):       # approved more than offered
        run(hostkeys.pin_approved(nb, "leaf1", [key, other], fetch=offering(key)))
    assert hostkeys.pins(device(nb)) == {22: [], 830: []}
    result = run(hostkeys.pin_approved(nb, "leaf1", [key + " comment"], fetch=offering(key)))
    assert result["fingerprints"] == [hostkeys.fingerprint(key)]
    assert hostkeys.pins(device(nb)) == {22: [key], 830: [key]}
    assert run(hostkeys.offer(nb, "leaf1", fetch=offering(key)))["unchanged"] is True
    run(hostkeys.forget_pins(nb, "leaf1"))
    assert hostkeys.pins(device(nb)) == {22: [], 830: []}


def test_pinning_from_the_api_needs_the_approver_role(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from orchestrator.app import app, current_account, get_accounts, get_netbox
    from tests.orchestrator.test_segments import FakeAccounts

    nb, accounts, key = example_netbox(), FakeAccounts(), rsa_key()
    monkeypatch.setattr(hostkeys, "fetch_keys", offering(key))
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    try:
        client = TestClient(app)
        assert client.post("/devices/leaf1/hostkeys/fetch").json()["offered"]["830"][0]["key"] == key
        app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="op", role="operator")
        assert client.put("/devices/leaf1/hostkeys", json={"keys": [key]}).status_code == 403
        app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="tester", role="approver")
        assert client.put("/devices/leaf1/hostkeys", json={"keys": [key]}).status_code == 200
        assert client.put("/devices/leaf1/hostkeys", json={"keys": []}).status_code == 400
        assert client.delete("/devices/leaf1/hostkeys").status_code == 200
    finally:
        app.dependency_overrides.pop(get_netbox, None)
        app.dependency_overrides.pop(get_accounts, None)
    assert [a for _, a, _ in accounts.events] == ["hostkey.pin", "hostkey.forget"]
