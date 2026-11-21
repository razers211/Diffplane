import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from orchestrator import auth, db
from orchestrator.app import app, current_account, current_user, get_accounts
from orchestrator.auth import COOKIE, Accounts, hash_password, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("correct horse")

    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert hash_password("correct horse") != stored  # salted


def test_an_older_weaker_hash_still_signs_in_and_is_upgraded(accounts):
    import hashlib
    import secrets

    salt = secrets.token_bytes(16)
    old = f"scrypt$16384$8$1${salt.hex()}${hashlib.scrypt(b'alice-password', salt=salt, dklen=32, n=2**14, r=8, p=1).hex()}"

    async def scenario():
        async with accounts.sessions() as session:
            await session.execute(update(db.User).where(db.User.username == "alice").values(password_hash=old))
            await session.commit()
        assert await accounts.login("alice", "alice-password") is not None
        async with accounts.sessions() as session:
            return await session.scalar(select(db.User.password_hash).where(db.User.username == "alice"))

    upgraded = asyncio.run(scenario())
    assert upgraded.startswith(f"scrypt${auth._SCRYPT['n']}$8$1$") and verify_password("alice-password", upgraded)


@pytest.fixture
def accounts(database_url) -> Accounts:
    accounts = Accounts(db.make_sessionmaker(db.make_engine(database_url, pooled=False)))
    asyncio.run(accounts.create_user("alice", "alice-password"))
    return accounts


def test_login_logout_and_audit(accounts):
    async def scenario():
        token = await accounts.login("alice", "alice-password")
        user = await accounts.user_for(token)
        await accounts.logout(token)
        after = await accounts.user_for(token)
        async with accounts.sessions() as session:
            actions = (await session.scalars(select(db.AuditEvent.action).order_by(db.AuditEvent.id))).all()
        return user, after, actions

    user, after, actions = asyncio.run(scenario())

    assert user.username == "alice" and after is None
    assert actions == ["user.create", "login"]


def test_wrong_password_is_refused_and_audited(accounts):
    async def scenario():
        token = await accounts.login("alice", "nope")
        async with accounts.sessions() as session:
            return token, (await session.scalars(select(db.AuditEvent.action))).all()

    token, actions = asyncio.run(scenario())

    assert token is None and "login.failed" in actions


def test_expired_session_is_refused(accounts):
    async def scenario():
        token = await accounts.login("alice", "alice-password")
        async with accounts.sessions() as session:
            await session.execute(update(db.Session).values(expires_at=db.now() - timedelta(seconds=1)))
            await session.commit()
        return await accounts.user_for(token)

    assert asyncio.run(scenario()) is None


def test_duplicate_user_and_short_password_are_refused(accounts):
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(accounts.create_user("alice", "another-password"))
    with pytest.raises(ValueError, match="at least 8"):
        asyncio.run(accounts.create_user("bob", "short"))


def test_http_login_sets_a_session_cookie_and_me_works(accounts, monkeypatch):
    # As deployed: HTTPS only, so the session cookie is Secure.
    monkeypatch.setenv("COOKIE_SECURE", "true")
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides[get_accounts] = lambda: accounts
    client = TestClient(app, base_url="https://testserver")

    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/login", json={"username": "alice", "password": "bad"}).status_code == 401
    response = client.post("/auth/login", json={"username": "alice", "password": "alice-password"})

    assert response.status_code == 200 and response.json() == {"username": "alice", "role": "viewer", "fabrics": None}
    cookie = response.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie
    assert client.get("/auth/me").json() == {"username": "alice", "role": "viewer", "fabrics": None, "source": "local"}
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401
    app.dependency_overrides.pop(get_accounts, None)


@pytest.mark.parametrize(
    "method, path",
    [("get", "/devices"), ("get", "/topology"), ("get", "/devices/x/config"), ("get", "/diff-jobs"),
     ("post", "/diff-jobs"), ("get", "/deploy-jobs"), ("post", "/deploy-jobs"), ("post", "/deploy-jobs/1/approve"),
     ("get", "/audit")],
)
def test_endpoints_need_a_login(accounts, method, path):
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides[get_accounts] = lambda: accounts

    response = getattr(TestClient(app), method)(path)

    assert response.status_code == 401
    app.dependency_overrides.pop(get_accounts, None)


# -- roles ----------------------------------------------------------------------------

def test_role_ladder():
    from orchestrator.auth import role_allows

    assert role_allows("approver", "operator") and role_allows("operator", "viewer")
    assert not role_allows("viewer", "operator") and not role_allows("operator", "approver")
    assert role_allows("admin", "approver") and not role_allows("approver", "admin")
    assert not role_allows("root", "viewer")


def test_roles_are_set_and_validated(accounts):
    asyncio.run(accounts.create_user("bob", "bob-password", "operator"))
    asyncio.run(accounts.set_role("alice", "approver"))

    async def roles():
        async with accounts.sessions() as session:
            return dict((await session.execute(select(db.User.username, db.User.role))).all())

    assert asyncio.run(roles()) == {"alice": "approver", "bob": "operator"}
    with pytest.raises(ValueError, match="role must be one of"):
        asyncio.run(accounts.set_role("alice", "root"))


@pytest.mark.parametrize("role, method, path, allowed", [
    ("viewer", "get", "/audit", True),
    ("viewer", "post", "/diff-jobs", False),
    ("viewer", "post", "/segments", False),
    ("viewer", "post", "/deploy-jobs", False),
    ("operator", "post", "/diff-jobs", True),
    ("operator", "post", "/deploy-jobs", True),
    ("operator", "post", "/deploy-jobs/1/approve", False),
    ("approver", "post", "/deploy-jobs/1/approve", True),
])
def test_routes_check_the_role(accounts, role, method, path, allowed):
    asyncio.run(accounts.create_user("carol", "carol-password", role))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides[get_accounts] = lambda: accounts
    try:
        client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        client.post("/auth/login", json={"username": "carol", "password": "carol-password"})
        response = client.get(path) if method == "get" else getattr(client, method)(path, json={})
        # Allowed requests get past the role check (and may fail later for other reasons).
        assert (response.status_code != 403) == allowed, response.text
        if not allowed:
            assert f"you are {role}" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_accounts, None)


def test_every_write_route_needs_operator_or_approver():
    from fastapi.routing import APIRoute

    from orchestrator.app import ADMIN, APPROVE, OPERATE, api_routes

    writes = [r for r in api_routes() if isinstance(r, APIRoute) and r.methods & {"POST", "PUT", "PATCH", "DELETE"}
              and not r.path.startswith("/auth/") and r.path != "/setup"]   # /setup: its one-time code
    assert len(writes) > 10
    for route in writes:
        checks = {d.dependency for d in route.dependencies}
        assert checks & {OPERATE.dependency, APPROVE.dependency, ADMIN.dependency}, route.path


def test_users_from_before_roles_become_approvers(postgres):
    from orchestrator.migrate import upgrade

    server, socket_dir = postgres
    server.psql("CREATE DATABASE roles_migration;")
    url = f"postgresql+asyncpg://postgres@/roles_migration?host={socket_dir}"
    upgrade(url, "0002")
    server.psql("\\c roles_migration\nINSERT INTO users (username, password_hash, is_active, created_at) "
                "VALUES ('old', 'x', true, now());")
    upgrade(url)

    async def roles():
        engine = db.make_engine(url, pooled=False)
        async with engine.connect() as conn:
            rows = (await conn.exec_driver_sql("SELECT username, role FROM users")).all()
        await engine.dispose()
        return dict(rows)

    assert asyncio.run(roles()) == {"old": "approver"}
    accounts = Accounts(db.make_sessionmaker(db.make_engine(url, pooled=False)))
    asyncio.run(accounts.create_user("new", "new-password"))
    assert asyncio.run(roles())["new"] == "viewer"


# -- accounts from the GUI --------------------------------------------------------------

def _client_as(accounts, username: str, password: str) -> TestClient:
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides[get_accounts] = lambda: accounts
    client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
    assert client.post("/auth/login", json={"username": username, "password": password}).status_code == 200
    return client


def test_admins_manage_accounts_and_the_last_admin_stays(accounts):
    asyncio.run(accounts.create_user("root", "root-password", "admin"))
    try:
        admin = _client_as(accounts, "root", "root-password")
        assert admin.post("/users", json={"username": "dave", "password": "dave-password", "role": "operator"}).status_code == 201
        assert [(u["username"], u["role"]) for u in admin.get("/users").json()] == [
            ("alice", "viewer"), ("dave", "operator"), ("root", "admin")]
        assert admin.post("/users", json={"username": "dave", "password": "x" * 9}).status_code == 400   # exists
        assert admin.patch("/users/dave", json={"role": "approver"}).status_code == 200
        assert admin.patch("/users/root", json={"role": "viewer"}).json()["detail"].endswith("make someone else admin first")
        assert admin.delete("/users/root").status_code == 400        # not yourself
        assert admin.patch("/users/dave", json={"password": "new-dave-password"}).status_code == 200
        assert admin.delete("/users/dave").status_code == 200
        assert "dave" not in [u["username"] for u in admin.get("/users").json()]

        viewer = _client_as(accounts, "alice", "alice-password")
        assert viewer.get("/users").status_code == 403
        assert viewer.post("/users", json={"username": "eve", "password": "eve-password", "role": "admin"}).status_code == 403
    finally:
        app.dependency_overrides.pop(get_accounts, None)


def test_everyone_changes_their_own_password(accounts):
    try:
        alice = _client_as(accounts, "alice", "alice-password")
        assert alice.post("/auth/password", json={"current": "wrong", "new": "brand-new-pass"}).status_code == 400
        assert alice.post("/auth/password", json={"current": "alice-password", "new": "short"}).status_code == 400
        assert alice.post("/auth/password", json={"current": "alice-password", "new": "brand-new-pass"}).status_code == 200
        assert alice.get("/auth/me").status_code == 401                  # signed out everywhere
        _client_as(accounts, "alice", "brand-new-pass")
    finally:
        app.dependency_overrides.pop(get_accounts, None)



def test_the_shipped_hashing_strength_is_n_2_17():
    # tests/conftest.py lowers it for speed; what ships must stay at OWASP's minimum.
    assert '_SCRYPT = {"n": 2**17, "r": 8, "p": 1}' in open(auth.__file__).read()
