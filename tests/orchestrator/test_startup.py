"""The orchestrator's own start-up with a platform database (the lifespan), which the other tests
skip by overriding dependencies: a mistake there stops the platform from starting at all."""

from fastapi.testclient import TestClient

from orchestrator.app import app


def test_the_orchestrator_starts_and_stops_with_a_database(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("NETBOX_URL", "http://127.0.0.1:9")   # nothing listens: NetBox down at start
    monkeypatch.setenv("NETBOX_TOKEN", "t" * 40)
    monkeypatch.setenv("NETBOX_TIMEOUT", "1")
    for name in ("PROMETHEUS_URL", "CLICKHOUSE_PASSWORD", "ZTP_DIR", "KNOWN_HOSTS_FILE", "ALERTMANAGER_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    saved = dict(app.state._state)   # other tests in this process see the state they expect
    try:
        with TestClient(app, base_url="https://testserver") as client:
            for part in ("jobs", "accounts", "settings", "deploys", "drafts", "snapshots", "intent_store", "drift"):
                assert getattr(app.state, part) is not None, part
            assert app.state.netbox.intent is None   # NetBox down: its contexts not adopted yet, nothing attached
            health = client.get("/health")
            assert health.status_code in (200, 503) and "netbox" in health.text.lower()
    finally:
        app.state._state.clear()
        app.state._state.update(saved)
