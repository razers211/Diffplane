"""Intent snapshots: the GUI-written intent of a fabric, kept at each verified deploy."""

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from orchestrator import db, fabric_ntp
from orchestrator.app import app, get_snapshots
from orchestrator.db import IntentSnapshot
from orchestrator.intent_snapshots import KEEP_LAST, IntentSnapshots, capture_intent
from orchestrator.segments import Segments
from tests.collector.test_collector import example_netbox


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb():
    netbox = example_netbox()
    segments = Segments(netbox)
    run(segments.create("example", "web", 10500, 500))
    run(segments.attach("example", "leaf1", "10GE1/0/3", 10500, None))
    run(fabric_ntp.set_snmp(netbox, "example", "hall 2", "noc@example.net"))
    return netbox


def test_the_snapshot_holds_the_guis_intent_only(nb):
    data = run(capture_intent(nb, "example"))
    assert [(s["name"], s["vni"], s["bd_id"]) for s in data["segments"]] == [("web", 10500, 500)]
    assert data["segments"][0]["attachments"] == [{"device": "leaf1", "interface": "10GE1/0/3", "vid": None}]
    assert data["fabric"]["snmp"] == {"location": "hall 2", "contact": "noc@example.net"}
    assert "asn" not in data["fabric"]                                       # structural: not GUI intent
    numbers = {t["number"] for trunks in data["trunks"].values() for t in trunks}
    assert numbers and all(t["m_lag"] for t in data["trunks"]["leaf1"])  # the YAML's M-LAG trunk, no peer-link


def test_snapshots_are_kept_listed_and_pruned(nb, database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    store = IntentSnapshots(sessions, nb)
    kept = run(store.take("example", job_id=42))
    assert kept["job_id"] == 42 and kept["segments"] == 1
    assert run(store.get("example", kept["id"]))["data"]["segments"][0]["name"] == "web"
    assert run(store.get("dc2", kept["id"])) is None

    async def old():
        async with sessions() as session:
            for i in range(KEEP_LAST + 3):
                session.add(IntentSnapshot(site="example", taken_at=db.now() - timedelta(days=200 - i), digest="x", data={}))
            await session.commit()
    run(old())
    assert run(store.prune("example")) == 4                                   # 24 kept points: the 4 oldest go
    assert run(store.list("example"))[0]["id"] == kept["id"]

    app.dependency_overrides[get_snapshots] = lambda: store
    try:
        client = TestClient(app, base_url="https://testserver")
        assert client.get("/fabrics/example/intent-snapshots").json()[0]["segments"] == 1
        assert client.get(f"/fabrics/example/intent-snapshots/{kept['id']}").json()["data"]["fabric"]["snmp"]["location"] == "hall 2"
        assert client.get("/fabrics/example/intent-snapshots/99999").status_code == 404
    finally:
        app.dependency_overrides.pop(get_snapshots, None)
