"""Platform settings from the GUI: live, secrets write-only, .env values as defaults."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, platform_settings
from orchestrator.app import app, current_account, current_user, get_accounts, get_settings
from orchestrator.auth import Accounts
from orchestrator.platform_settings import PlatformSettings


@pytest.fixture
def store(database_url, tmp_path, monkeypatch):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    env = {"COLLECTOR_LOGIN": "reader", "COLLECTOR_PASSWORD": "from-dot-env", "SNMP_COMMUNITY": "env-community"}
    monkeypatch.setattr(platform_settings, "_STARTED", {**{k: None for k in platform_settings.BY_KEY}, **env})
    settings = PlatformSettings(sessions, shared=str(tmp_path), env=dict(env))
    asyncio.run(settings.load())
    return settings, env, tmp_path, sessions


def test_a_secret_is_set_used_and_never_shown(store):
    settings, _, shared, _ = store
    asyncio.run(settings.set("COLLECTOR_PASSWORD", "new-secret-1", "root"))
    assert settings.env["COLLECTOR_PASSWORD"] == "new-secret-1"              # what Annet and redaction read
    assert json.loads((shared / "collector.json").read_text()) == {"login": "reader", "password": "new-secret-1"}
    view = json.dumps(settings.view())
    assert "new-secret-1" not in view and "from-dot-env" not in view and "env-community" not in view
    row = next(r for r in settings.view()["settings"] if r["key"] == "COLLECTOR_PASSWORD")
    assert row["source"] == "gui" and row["value"] is None and row["secret"]

    asyncio.run(settings.reset("COLLECTOR_PASSWORD", "root"))            # back to the .env value
    assert settings.env["COLLECTOR_PASSWORD"] == "from-dot-env"
    assert next(r for r in settings.view()["settings"] if r["key"] == "COLLECTOR_PASSWORD")["source"] == "env"


def test_values_are_checked(store):
    settings = store[0]
    for key, value, message in [
        ("DRIFT_INTERVAL_SECONDS", "5", "from 60 to 86400"), ("SNMP_COMMUNITY", "a b c d", "no spaces"),
        ("ZTP_DHCP_NETWORK", "10.0.0.0/33", "Management network"), ("COLLECTOR_LOGIN", "bad name!", "letters"),
        ("NETBOX_TOKEN", "x" * 40, "not a setting the GUI may change"), ("TLS_HOSTNAMES", " , ", "names or addresses"),
    ]:
        with pytest.raises(ValueError, match=message):
            asyncio.run(settings.set(key, value, "root"))
    asyncio.run(settings.set("ZTP_DHCP_NETWORK", "172.24.0.9/24", "root"))
    assert settings.env["ZTP_DHCP_NETWORK"] == "172.24.0.0/24"                # cleaned


def test_the_gui_values_survive_a_restart(store):
    settings, env, shared, sessions = store
    asyncio.run(settings.set("DRIFT_INTERVAL_SECONDS", "600", "root"))
    again = PlatformSettings(sessions, shared=str(shared), env=dict(env))
    asyncio.run(again.load())
    assert again.env["DRIFT_INTERVAL_SECONDS"] == "600"


def test_only_admins_see_and_change_settings(store, database_url):
    settings = store[0]
    accounts = Accounts(db.make_sessionmaker(db.make_engine(database_url, pooled=False)))
    asyncio.run(accounts.create_user("root", "root-password", "admin"))
    asyncio.run(accounts.create_user("bob", "bob-password", "approver"))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides.update({get_accounts: lambda: accounts, get_settings: lambda: settings})
    try:
        client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        client.post("/auth/login", json={"username": "bob", "password": "bob-password"})
        assert client.get("/settings").status_code == 403
        assert client.put("/settings/SNMP_COMMUNITY", json={"value": "another-one"}).status_code == 403
        client.post("/auth/login", json={"username": "root", "password": "root-password"})
        assert client.put("/settings/SNMP_COMMUNITY", json={"value": "another-one"}).status_code == 200
        body = client.get("/settings").text
        assert "another-one" not in body and "env-community" not in body
        assert client.put("/settings/SNMP_COMMUNITY", json={"value": "x"}).status_code == 400
        assert client.delete("/settings/SNMP_COMMUNITY").status_code == 200
    finally:
        app.dependency_overrides.pop(get_accounts, None)
        app.dependency_overrides.pop(get_settings, None)
