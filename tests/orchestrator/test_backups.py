"""Switch config backups: encrypted versions, unchanged configs not kept twice, pruning,
everything shown masked."""

import asyncio
import base64
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from orchestrator import backups, db
from orchestrator.app import app, get_backups
from orchestrator.backups import BackupError, Backups, mask
from orchestrator.db import ConfigVersion
from tests.collector.test_collector import example_netbox

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
RUNNING = """!Software Version V300R023C00SPC500
!Last configuration was updated at 2026-10-12 10:00:00+00:00
sysname leaf1
#
dfs-group 1
 authentication-mode hmac-sha256 password %^%#abcDEF123%^%#
#
snmp-agent community read cipher %^%#qwerty%^%# alias ro-1234abcd
#
aaa
 local-user netconf password irreversible-cipher $1d$secret$hash
#
interface LoopBack0
 ip address 10.10.0.2 255.255.255.255
#
return
"""


def run(coro):
    return asyncio.run(coro)


class FakeAnnet:
    def __init__(self):
        self.config = RUNNING
        self.reads = 0

    async def current(self, device):
        self.reads += 1
        return self.config


@pytest.fixture
def store(database_url):
    nb = example_netbox()
    leaf = run(nb.find_one("dcim/devices", name="leaf1"))
    run(nb.update("dcim/devices", leaf["id"], {"status": "active", "custom_fields": {"ssh_host_keys": {"22": ["ssh-rsa AAAA"]}}}))
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    annet = FakeAnnet()
    return Backups(sessions, annet, nb, env={"CONFIG_BACKUP_KEY": KEY}), annet, sessions


def test_secrets_are_masked():
    shown = mask(RUNNING)
    for secret in ("abcDEF123", "qwerty", "$1d$secret$hash"):
        assert secret not in shown
    assert "alias ro-1234abcd" in shown and "ip address 10.10.0.2 255.255.255.255" in shown
    assert "authentication-mode hmac-sha256 password ******" in shown
    assert "local-user netconf password irreversible-cipher ******" in shown


def test_versions_are_encrypted_kept_once_and_diffed(store):
    backups_, annet, sessions = store
    first = run(backups_.capture("leaf1", "deploy", job_id=7))
    assert first["new"] and first["reason"] == "deploy" and first["site"] == "example"

    async def stored():
        async with sessions() as session:
            return (await session.get(ConfigVersion, first["id"])).config
    assert b"abcDEF123" not in run(stored()) and b"sysname" not in run(stored())       # encrypted at rest

    annet.config = RUNNING.replace("10:00:00", "11:00:00")                           # only a comment changed
    assert run(backups_.capture("leaf1", "scheduled"))["new"] is False
    annet.config = RUNNING.replace("10.10.0.2", "10.10.0.9")
    second = run(backups_.capture("leaf1", "manual", user="op"))
    assert second["new"] and [v["id"] for v in run(backups_.versions("leaf1"))] == [second["id"], first["id"]]

    diff = run(backups_.diff("leaf1", first["id"], second["id"]))["diff"]
    assert "- ip address 10.10.0.2 255.255.255.255" in diff and "+ ip address 10.10.0.9 255.255.255.255" in diff
    assert "abcDEF123" not in run(backups_.shown("leaf1", first["id"]))["config"]
    with pytest.raises(BackupError, match="no version"):
        run(backups_.shown("leaf2", first["id"]))


def test_no_key_no_backup_and_a_changed_key_is_reported(store):
    backups_, annet, sessions = store
    version = run(backups_.capture("leaf1", "manual"))
    off = Backups(sessions, annet, backups_.netbox, env={})
    assert "CONFIG_BACKUP_KEY is not set" in off.problem()
    with pytest.raises(BackupError, match="not set"):
        run(off.capture("leaf1", "manual"))
    other = Backups(sessions, annet, backups_.netbox, env={"CONFIG_BACKUP_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode()})
    with pytest.raises(BackupError, match="changed since"):
        run(other.shown("leaf1", version["id"]))
    with pytest.raises(BackupError, match="not an active switch"):
        run(backups_.capture("leaf2", "manual"))      # planned


def test_old_versions_go_but_the_last_twenty_stay(store):
    backups_, _, sessions = store

    async def fill():
        async with sessions() as session:
            for i in range(25):
                session.add(ConfigVersion(device="leaf1", site="example", taken_at=db.now() - timedelta(days=200 - i),
                                          reason="scheduled", digest=str(i), lines=1, config=b"x"))
            session.add(ConfigVersion(device="leaf1", site="example", taken_at=db.now(), reason="manual",
                                      digest="new", lines=1, config=b"x"))
            await session.commit()
    run(fill())
    assert run(backups_.prune("leaf1")) == 6                      # 26 versions: the 6 oldest go
    assert len(run(backups_.versions("leaf1"))) == backups.KEEP_LAST


def test_api_shows_versions_masked(store):
    backups_, _, _ = store
    version = run(backups_.capture("leaf1", "manual"))
    app.dependency_overrides[get_backups] = lambda: backups_
    try:
        client = TestClient(app, base_url="https://testserver")
        assert client.get("/devices/leaf1/configs").json()["versions"][0]["id"] == version["id"]
        body = client.get(f"/devices/leaf1/configs/{version['id']}").text
        assert "qwerty" not in body and "sysname leaf1" in body
        assert client.get("/devices/leaf1/configs/9999").status_code == 400
    finally:
        app.dependency_overrides.pop(get_backups, None)
