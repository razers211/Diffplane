"""First-time setup: the first admin, with the one-time code from the orchestrator's log."""

import asyncio
import io

import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.app import app, current_account, current_user, get_accounts, get_setup
from orchestrator.auth import Accounts
from orchestrator.first_setup import MAX_TRIES, FirstSetup


@pytest.fixture
def empty(database_url):
    accounts = Accounts(db.make_sessionmaker(db.make_engine(database_url, pooled=False)))
    log = io.StringIO()
    setup = FirstSetup(out=log)
    asyncio.run(setup.start(accounts))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides.update({get_accounts: lambda: accounts, get_setup: lambda: setup})
    try:
        yield TestClient(app, base_url="https://testserver"), setup, log
    finally:
        app.dependency_overrides.pop(get_accounts, None)
        app.dependency_overrides.pop(get_setup, None)


def test_the_first_admin_needs_the_code_from_the_log(empty):
    client, setup, log = empty
    assert setup.code and setup.code in log.getvalue()
    assert client.get("/setup").json() == {"needed": True}

    wrong = client.post("/setup", json={"code": "0000-0000", "username": "root", "password": "root-password"})
    assert wrong.status_code == 400 and "log" in wrong.json()["detail"]
    ok = client.post("/setup", json={"code": setup.code.lower(), "username": "root", "password": "root-password"})
    assert ok.status_code == 200 and ok.json() == {"username": "root", "role": "admin"}
    assert client.get("/auth/me").json() == {"username": "root", "role": "admin", "fabrics": None, "source": "local"}   # signed in

    assert client.get("/setup").json() == {"needed": False}
    again = client.post("/setup", json={"code": "anything", "username": "eve", "password": "eve-password"})
    assert again.status_code == 409   # over for good once an account exists


def test_too_many_wrong_codes_make_a_new_one(empty):
    client, setup, log = empty
    first = setup.code
    for _ in range(MAX_TRIES):
        client.post("/setup", json={"code": "0000-0000", "username": "root", "password": "root-password"})
    assert setup.code != first and setup.code in log.getvalue()
    assert client.post("/setup", json={"code": first, "username": "root", "password": "root-password"}).status_code == 400
