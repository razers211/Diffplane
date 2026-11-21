"""Security headers, the same-origin check on writes, and the sign-in throttle."""

from fastapi.testclient import TestClient

from orchestrator.app import app, get_accounts
from orchestrator.auth import Throttle


class Refuses:
    async def login(self, username, password):
        return None


def test_answers_carry_the_security_headers():
    response = TestClient(app).get("/auth/me")   # any answer: here 401, no NetBox needed
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_a_write_from_another_site_is_refused_before_anything_runs():
    client = TestClient(app, base_url="https://fabric.example")
    body = {"username": "a", "password": "b"}
    refused = client.post("/auth/login", json=body, headers={"origin": "https://evil.example"})
    assert refused.status_code == 403
    app.dependency_overrides[get_accounts] = lambda: Refuses()
    try:   # same origin, or no Origin header (not a browser): goes on to the login itself
        assert client.post("/auth/login", json=body, headers={"origin": "https://fabric.example"}).status_code == 401
        assert client.post("/auth/login", json=body).status_code == 401
    finally:
        app.dependency_overrides.pop(get_accounts, None)


def test_the_throttle_waits_after_the_limit_and_forgets_after_the_window():
    throttle = Throttle(limit=3, window=60.0)
    for t in (0.0, 1.0, 2.0):
        assert throttle.wait("10.0.0.1", t) == 0
        throttle.failed("10.0.0.1", t)
    assert throttle.wait("10.0.0.1", 3.0) == 57.0
    assert throttle.wait("10.0.0.2", 3.0) == 0   # per address: another client is not held up
    assert throttle.wait("10.0.0.1", 61.0) == 0   # the first failure left the window


def test_too_many_failed_sign_ins_answer_429():
    app.dependency_overrides[get_accounts] = lambda: Refuses()
    app.state.throttle = Throttle(limit=2)
    try:
        client = TestClient(app)
        body = {"username": "a", "password": "b"}
        assert [client.post("/auth/login", json=body).status_code for _ in range(3)] == [401, 401, 429]
    finally:
        app.dependency_overrides.pop(get_accounts, None)
        del app.state.throttle


def test_metrics_need_prometheus_token(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    assert client.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 401   # none set: refused
    monkeypatch.setenv("METRICS_TOKEN", "right")
    for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "right"}):
        response = client.get("/metrics", headers=header)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
