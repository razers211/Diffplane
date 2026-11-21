"""Every API route, walked: without a session it answers 401 (except the few public ones), and
every role below the one the route declares gets 403."""

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.app import _intent_unfrozen, api_routes, app, current_account, current_user, get_accounts
from orchestrator.auth import ROLES

# Reachable without a session: health, signing in and out, first-time setup. (/metrics answers
# 401 without Prometheus' token: test_web_security.)
PUBLIC = {("GET", "/health"), ("POST", "/auth/login"), ("POST", "/auth/logout"),
          ("GET", "/setup"), ("POST", "/setup"), ("GET", "/")}


def _routes():
    for route in api_routes():
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD"}):
                yield method, route


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def _required(route: APIRoute) -> str | None:
    """The highest role a `require(...)` dependency of the route asks for."""
    roles = []
    for dep in route.dependant.dependencies:
        call = dep.call
        if getattr(call, "__qualname__", "") == "require.<locals>.check":
            roles.append(dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__)))["role"])
    return max(roles, key=ROLES.index) if roles else None


class NoSessions:
    async def user_for(self, token):
        return None


@pytest.fixture
def client():
    app.dependency_overrides.pop(current_user, None)   # the conftest signs a tester in
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides[get_accounts] = lambda: NoSessions()
    app.dependency_overrides[_intent_unfrozen] = lambda: None
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_accounts, None)
    app.dependency_overrides.pop(_intent_unfrozen, None)
    app.dependency_overrides.pop(current_account, None)


def test_every_route_but_the_public_ones_needs_a_session(client):
    open_routes = [(m, r.path) for m, r in _routes()
                   if (m, r.path) not in PUBLIC and client.request(m, _url(r.path)).status_code != 401]
    assert open_routes == []


def test_every_role_below_a_routes_role_is_refused(client):
    checked, wrong = 0, []
    for method, route in _routes():
        required = _required(route)
        if required is None:
            continue
        for role in ROLES[:ROLES.index(required)]:
            app.dependency_overrides[current_account] = lambda role=role: db.User(
                username="someone", role=role, fabrics=None, is_active=True, source="local")
            status = client.request(method, _url(route.path)).status_code
            checked += 1
            if status != 403:
                wrong.append((method, route.path, role, status))
    assert checked > 100 and wrong == []
