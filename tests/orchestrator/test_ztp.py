"""Zero-touch provisioning: register, dnsmasq files, watch, approve."""

import asyncio
from pathlib import Path

import paramiko
import pytest
from fastapi.testclient import TestClient

from orchestrator import hostkeys, ztp as zt
from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
from orchestrator.fabric_build import FabricBuilder
from orchestrator.lifecycle import Lifecycle
from orchestrator.segments import SegmentError
from seed.model import load
from tests.helpers import seed
from tests.orchestrator.test_segments import FakeAccounts
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]
ENV = {"GNETCLI_DEV_LOGIN": "netconf", "GNETCLI_DEV_PASSWORD": "Annet-pw1", "COLLECTOR_LOGIN": "netconf",
       "COLLECTOR_PASSWORD": "Annet-pw1", "ZTP_ADMIN_LOGIN": "admin", "ZTP_ADMIN_PASSWORD": "Admin-pw2",
       "ZTP_DHCP_INTERFACE": "enp0s31f6", "ZTP_DHCP_NETWORK": "172.24.0.0/24", "ZTP_SERVER_ADDRESS": "172.24.0.50"}


def run(coro):
    return asyncio.run(coro)


def rsa_key() -> str:
    key = paramiko.RSAKey.generate(2048)
    return f"{key.get_name()} {key.get_base64()}"


def offering(key):
    return lambda host, port: hostkeys.Offer([key], ["rsa-sha2-512"])


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    return netbox


@pytest.fixture
def ztp(nb, tmp_path) -> zt.Ztp:
    return zt.Ztp(nb, directory=str(tmp_path), env=ENV)


def device(nb, name):
    return next(d for d in nb.objects["dcim/devices"].values() if d["name"] == name)


def test_mac_forms():
    for text in ("0200-FC12-3456", "02:00:fc:12:34:56", "02-00-FC-12-34-56", "0200.fc12.3456"):
        assert zt.normalize_mac(text) == "02:00:fc:12:34:56"
    assert zt.vrp_mac("02:00:fc:12:34:56") == "0200-fc12-3456"
    for bad in ("0200-fc12-345", "zz:e0:fc:12:34:56", ""):
        with pytest.raises(SegmentError, match="not a MAC"):
            zt.normalize_mac(bad)


def test_accounts_are_the_platform_ones_plus_the_admin():
    assert zt.accounts(ENV) == [("admin", "Admin-pw2"), ("netconf", "Annet-pw1")]   # one netconf, not two
    with pytest.raises(SegmentError, match="ZTP_ADMIN_PASSWORD is not set"):
        zt.accounts({**ENV, "ZTP_ADMIN_PASSWORD": ""})
    with pytest.raises(SegmentError, match="different passwords"):
        zt.accounts({**ENV, "COLLECTOR_PASSWORD": "other"})
    with pytest.raises(SegmentError, match="no platform account"):
        zt.accounts({})


def test_first_boot_config_is_the_lab_bring_up():
    reg = {"mac": "02:00:fc:12:34:56", "address": "172.24.0.110/24", "gateway": "172.24.0.1", "interface": "MEth0/0/0"}
    assert zt.first_boot_config("leaf2", reg, [("netconf", "pw")]) == """\
sysname leaf2
#
aaa
 local-user netconf password irreversible-cipher pw
 local-user netconf password-force-change disable
 local-user netconf privilege level 3
 local-user netconf service-type ssh
#
interface MEth0/0/0
 ip binding vpn-instance _management_vpn_
 ip address 172.24.0.110 255.255.255.0
#
ip route-static vpn-instance _management_vpn_ 0.0.0.0 0.0.0.0 172.24.0.1
#
stelnet server enable
snetconf server enable
netconf
 protocol inbound ssh port 830
ssh server-source -i MEth0/0/0
undo ssh server-source all-interface
undo ssh ipv6 server-source all-interface
ssh server rsa-key min-length 3072
ssh authorization-type default aaa
ssh server cipher aes256_gcm aes128_gcm aes256_ctr aes192_ctr aes128_ctr
ssh server hmac sha2_512 sha2_256
ssh server key-exchange dh_group_exchange_sha256 dh_group16_sha512 curve25519_sha256
#
return
"""
    assert "MAC=0200-fc12-3456" in zt.intermediate_file("leaf2", reg, "172.24.0.50")
    assert "FILESERVER=tftp://172.24.0.50/" in zt.intermediate_file("leaf2", reg, "172.24.0.50")


def test_dnsmasq_answers_only_registered_switches_with_fixed_addresses():
    conf = zt.dnsmasq_conf(ENV)
    for line in ("interface=enp0s31f6", "dhcp-range=172.24.0.0,static,255.255.255.0", "dhcp-ignore=tag:!known",
                 "port=0"):
        assert line in conf.splitlines(), line
    assert "enable-tftp" not in conf   # our tftpd serves the files (each address its own folder)
    assert zt.dnsmasq_conf({**ENV, "ZTP_DHCP_INTERFACE": ""}) == ""   # not configured: dnsmasq stays off


def test_register_writes_each_switch_only_its_own_files(nb, ztp, tmp_path):
    run(ztp.register("leaf2", "0200-FC12-3456", "172.24.0.110/24", "172.24.0.1"))
    assert device(nb, "leaf2")["custom_fields"]["ztp"]["mac"] == "02:00:fc:12:34:56"
    assert (tmp_path / "hosts").read_text() == "02:00:fc:12:34:56,set:ztp_leaf2,172.24.0.110\n"
    assert (tmp_path / "opts").read_text().splitlines() == [
        "tag:ztp_leaf2,3,172.24.0.1", "tag:ztp_leaf2,66,172.24.0.50", "tag:ztp_leaf2,67,ztp.ini"]
    folder = tmp_path / "tftp" / "172.24.0.110"
    assert sorted(p.name for p in folder.iterdir()) == ["leaf2.cfg", "ztp.ini"]
    assert "local-user admin password irreversible-cipher Admin-pw2" in (folder / "leaf2.cfg").read_text()
    assert (tmp_path / "dnsmasq.conf").read_text() == zt.dnsmasq_conf(ENV)
    assert (tmp_path / "tftp-listen").read_text() == "172.24.0.50\n"

    preview = run(ztp.preview("leaf2"))
    assert "Admin-pw2" not in preview["config"] and "irreversible-cipher ******" in preview["config"]

    run(ztp.unregister("leaf2"))
    assert not folder.exists() and (tmp_path / "hosts").read_text() == ""


def test_a_registration_is_served_only_inside_its_window(nb, tmp_path):
    ztp = zt.Ztp(nb, directory=str(tmp_path), env={**ENV, "ZTP_WINDOW_HOURS": "1"})
    run(ztp.register("leaf2", "0200-FC12-3456", "172.24.0.110/24", "172.24.0.1"))
    folder = tmp_path / "tftp" / "172.24.0.110"
    assert folder.exists() and (tmp_path / "hosts").read_text()

    reg = device(nb, "leaf2")["custom_fields"]["ztp"]
    reg["registered_at"] -= 3601   # an hour and a second later
    assert zt.expired(reg, ztp.env) and not zt.expired({**reg, "offered": {"22": []}}, ztp.env)
    run(ztp.publish())
    assert not folder.exists() and (tmp_path / "hosts").read_text() == ""   # DHCP silent, files gone
    assert run(ztp.watch_once(probe=lambda ip: pytest.fail("an expired switch is not probed"))) == []

    run(ztp.register("leaf2", "0200-FC12-3456", "172.24.0.110/24", "172.24.0.1"))   # a new window
    assert folder.exists()


def test_register_refusals(nb, ztp):
    run(ztp.register("leaf2", "0200-fc12-3456", "172.24.0.110/24", "172.24.0.1"))
    run(FabricBuilder(nb, "example").add_spine("spine2", "CE6881H-48S6CQ", []))
    for args, message in [
        (("leaf1", "0200-fc12-0001", "172.24.0.111/24", "172.24.0.1"), "only a planned switch"),
        (("spine1", "0200-fc12-3456", "172.24.0.111/24", "172.24.0.1"), "already registered for leaf2"),
        (("spine1", "0200-fc12-0001", "172.24.0.110/24", "172.24.0.1"), "already registered for leaf2"),
        (("spine1", "0200-fc12-0001", "172.24.0.11/24", "172.24.0.1"), "already a management address"),
        (("spine1", "0200-fc12-0001", "172.24.0.111", "172.24.0.1"), "with its mask"),
        (("spine1", "0200-fc12-0001", "172.24.0.111/24", "10.73.0.1"), "gateway"),
        (("spine1", "0200-fc12-0001", "10.73.0.111/24", "10.73.0.1"), "outside the ZTP DHCP network"),
        (("spine1", "bad", "172.24.0.111/24", "172.24.0.1"), "not a MAC"),
    ]:
        with pytest.raises(SegmentError, match=message):
            run(ztp.register(*args))
    with pytest.raises(SegmentError, match="no platform account"):
        run(zt.Ztp(nb, env={}).register("spine2", "0200-fc12-0002", "172.24.0.112/24", "172.24.0.1"))
    # A registered switch is approved through ZTP, not activated by hand, and not removed.
    with pytest.raises(SegmentError, match="zero-touch"):
        run(Lifecycle(nb).activate("leaf2", "172.24.0.110/24"))


def test_watch_then_approve_pins_activates_and_cleans_up(nb, ztp, tmp_path):
    key, other = rsa_key(), rsa_key()
    run(ztp.register("spine1", "0200-fc12-0001", "172.24.0.111/24", "172.24.0.1"))
    assert run(ztp.watch_once(probe=lambda ip: False, fetch=offering(key))) == []          # not up yet
    assert run(ztp.watch_once(probe=lambda ip: ip == "172.24.0.111", fetch=offering(key))) == ["spine1"]
    reg = device(nb, "spine1")["custom_fields"]["ztp"]
    assert reg["offered"] == {"22": [key], "830": [key]} and reg["reported_at"]
    assert run(ztp.watch_once(probe=lambda ip: True, fetch=offering(other))) == []         # reported once

    with pytest.raises(SegmentError, match="did not give"):                                  # the key changed
        run(ztp.approve("spine1", [key], fetch=offering(other)))
    assert device(nb, "spine1")["status"] == "planned"
    result = run(ztp.approve("spine1", [key], fetch=offering(key)))
    assert result["fingerprints"] == [hostkeys.fingerprint(key)]
    spine = device(nb, "spine1")
    assert spine["status"] == "active" and spine["custom_fields"]["ztp"] is None
    assert spine["custom_fields"]["ssh_host_keys"] == {"22": [key], "830": [key]}
    assert nb.objects["ipam/ip-addresses"][spine["primary_ip4"]]["address"] == "172.24.0.111/24"
    assert not (tmp_path / "tftp" / "172.24.0.111").exists()


def test_a_registered_switch_cannot_be_removed(nb, ztp):
    add = FabricBuilder(nb, "example")
    run(add.add_spine("spine2", "CE6881H-48S6CQ", []))
    run(ztp.register("spine2", "0200-fc12-0002", "172.24.0.112/24", "172.24.0.1"))
    with pytest.raises(SegmentError, match="unregister it first"):
        run(FabricBuilder(nb, "example").remove("spine2"))


def test_api_register_preview_and_approve_roles(nb, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from orchestrator.app import current_account

    for name, value in {**ENV, "ZTP_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    key = rsa_key()
    monkeypatch.setattr(hostkeys, "fetch_keys", offering(key))
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None
    try:
        client = TestClient(app)
        body = {"mac": "0200-fc12-0001", "address": "172.24.0.111/24", "gateway": "172.24.0.1"}
        assert client.put("/devices/spine1/ztp", json=body).status_code == 200
        assert client.put("/devices/leaf1/ztp", json=body).status_code == 400
        files = client.get("/devices/spine1/ztp/files").json()
        assert "Admin-pw2" not in files["config"] and "sysname spine1" in files["config"]
        listed = client.get("/ztp").json()
        assert listed["configured"] is True and set(listed["registrations"]) == {"spine1"}
        run(zt.Ztp(nb).watch_once(probe=lambda ip: True))
        app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="op", role="operator")
        assert client.post("/devices/spine1/ztp/approve", json={"keys": [key]}).status_code == 403
        app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="tester", role="approver")
        assert client.post("/devices/spine1/ztp/approve", json={"keys": [key]}).status_code == 200
        assert device(nb, "spine1")["status"] == "active"
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["ztp.register", "ztp.approve"]
