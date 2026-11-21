"""The "what changed" timeline: transitions between two reads, alerts firing and resolved, the
store and GET /events with deploys and intent changes from the audit trail."""

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from orchestrator import db, events, health
from orchestrator.app import app, get_events, get_netbox
from orchestrator.auth import audit
from tests.collector.test_collector import example_netbox
from tests.orchestrator.test_health import Prom, facts


def run(coro):
    return asyncio.run(coro)


def read(f: health.Facts) -> dict:
    return events.states("east", f, health.fabric_health("east", f))


def test_the_first_read_is_a_baseline_then_changes_become_events():
    up = facts(in_use={"leaf1": {"10GE1/0/1"}}, oper={("leaf1", "10GE1/0/1"): True})
    down = facts(in_use={"leaf1": {"10GE1/0/1"}}, oper={("leaf1", "10GE1/0/1"): False})
    assert events.transitions(read(up), read(up)) == []
    went = events.transitions(read(up), read(down))
    link = next(e for e in went if e["kind"] == "link")
    assert link == {"site": "east", "device": "leaf1", "kind": "link", "subject": "10GE1/0/1", "state": "down",
                    "previous": "up", "severity": "problem", "detail": None}
    score = next(e for e in went if e["kind"] == "health" and e["device"] == "leaf1")
    assert (score["state"], score["previous"], score["detail"], score["severity"]) == (
        "warning 80", "good 100", "80: 10GE1/0/1 is down", "problem")
    back = events.transitions(read(down), read(up))
    assert {(e["kind"], e["state"], e["severity"]) for e in back} >= {("link", "up", "recovered"), ("health", "good 100", "recovered")}


def test_expected_down_and_unused_ports_are_not_problems():
    f = facts(in_use={"leaf1": {"100GE1/0/1"}}, oper={("leaf1", "100GE1/0/1"): True, ("leaf1", "10GE1/0/9"): True},
              expected_down={("leaf1", "100GE1/0/1")})
    g = facts(in_use={"leaf1": {"100GE1/0/1"}}, oper={("leaf1", "100GE1/0/1"): False, ("leaf1", "10GE1/0/9"): False},
              expected_down={("leaf1", "100GE1/0/1")})
    went = events.transitions(read(f), read(g))
    assert [(e["subject"], e["severity"]) for e in went] == [("100GE1/0/1", "change")]   # 10GE1/0/9 is not in use


def test_a_switch_no_longer_polled_records_that_alone():
    f = facts(in_use={"leaf1": {"10GE1/0/1"}}, oper={("leaf1", "10GE1/0/1"): True})
    g = facts(in_use={"leaf1": {"10GE1/0/1"}}, oper={}, collected={"leaf2", "spine1"})
    went = {(e["kind"], e["subject"], e["state"]) for e in events.transitions(read(f), read(g)) if e["device"] == "leaf1"}
    assert went == {("polling", "collector", "not polled"), ("health", "score", "warning 60")}   # 100 - 40


def test_alerts_fire_and_resolve():
    firing = [({"__name__": "ALERTS", "alertname": "FabricPowerModuleFailed", "alertstate": "firing", "device": "leaf1",
                "module": "PWR:1/1", "severity": "critical", "job": "collector"}, 0.0, 1.0),
              ({"__name__": "ALERTS", "alertname": "FabricOpticRxPower", "alertstate": "pending", "device": "leaf1"}, 0.0, 1.0)]
    now = events.alert_states(firing, {"leaf1": "example"})
    assert list(now) == [("example", "leaf1", "alert", "FabricPowerModuleFailed (module=PWR:1/1)")]   # pending is not firing
    fired = events.transitions({}, now)
    assert [(e["state"], e["severity"], e["detail"]) for e in fired] == [("firing", "problem", "critical")]
    resolved = events.transitions(now, {})
    assert [(e["state"], e["previous"], e["severity"]) for e in resolved] == [("resolved", "firing", "recovered")]


def test_the_watcher_reads_the_lab_and_records_only_changes(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    store = events.EventStore(sessions)
    oper = {"10GE1/0/3": True, "100GE1/0/1": False, "Eth-Trunk1": False}
    prom = Prom(oper)

    async def report():
        return [{"device": "leaf1", "state": "in_sync"}]

    watcher = events.EventWatcher(store, example_netbox(), prom, report, max_age=120)
    assert run(watcher.run_once()) == 0                         # baseline
    assert run(watcher.run_once()) == 0                         # nothing changed
    prom.oper = {**oper, "Eth-Trunk1": True}
    assert run(watcher.run_once()) >= 1
    rows = run(store.timeline({"example"}, kind="link"))
    assert [(r["device"], r["subject"], r["previous"], r["state"], r["severity"]) for r in rows] == [
        ("leaf1", "Eth-Trunk1", "down", "up", "recovered")]
    recent = watcher.recent("example")                              # the GUI's health, as last read
    assert recent["score"] == watcher.latest["example"][1]["score"] and watcher.recent("east") is None
    watcher.latest["example"] = (watcher.latest["example"][0] - 2 * watcher.interval - 16, recent)
    assert watcher.recent("example") is None                        # too old: computed again


def test_timeline_merges_changes_and_keeps_to_the_fabric(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    store = events.EventStore(sessions)
    run(store.record([{"site": "example", "device": "leaf1", "kind": "link", "subject": "10GE1/0/3", "state": "down",
                       "previous": "up", "severity": "problem", "detail": None},
                      {"site": "east", "device": "east-leaf1", "kind": "link", "subject": "10GE1/0/3", "state": "down",
                       "previous": "up", "severity": "problem", "detail": None}]))
    run(audit(sessions, "alice", "segment.attach", detail="example leaf1 10GE1/0/3 vni=10500"))
    run(audit(sessions, "alice", "login"))
    run(audit(sessions, "alice", "segment.create", detail="east web vni=10500"))

    rows = run(store.timeline({"example"}, names={"leaf1"}))
    assert [(r["kind"], r["subject"]) for r in rows] == [("change", "segment.attach"), ("link", "10GE1/0/3")]
    assert [r["subject"] for r in run(store.timeline({"example"}, kind="change", names={"leaf1"}))] == ["segment.attach"]
    assert len(run(store.timeline(None))) == 4                       # both fabrics, no sign-ins

    async def age():
        async with sessions() as session:
            row = (await session.get(db.Event, 1))
            row.at = db.now() - timedelta(days=91)
            await session.commit()
    run(age())
    assert run(store.purge(90)) == 1

    app.dependency_overrides[get_events] = lambda: store
    app.dependency_overrides[get_netbox] = lambda: example_netbox()
    try:
        body = TestClient(app, base_url="https://testserver").get("/events", params={"site": "example"}).json()
        assert [r["subject"] for r in body] == ["segment.attach"]
    finally:
        app.dependency_overrides.pop(get_events, None)
        app.dependency_overrides.pop(get_netbox, None)


def test_every_score_change_is_an_event_worse_or_better():
    """A port going down shows as a score change even where the level stays (leaf1 sits at critical)."""
    in_use = {"leaf1": {"10GE1/0/1", "10GE1/0/2", "10GE1/0/3"}}
    low = facts(in_use=in_use, oper={("leaf1", p): False for p in ("10GE1/0/1", "10GE1/0/2")} | {("leaf1", "10GE1/0/3"): True})
    lower = facts(in_use=in_use, oper={("leaf1", p): False for p in ("10GE1/0/1", "10GE1/0/2", "10GE1/0/3")})
    worse = next(e for e in events.transitions(read(low), read(lower)) if e["kind"] == "health" and e["device"] == "leaf1")
    assert (worse["previous"], worse["state"], worse["severity"]) == ("warning 60", "critical 40", "problem")
    better = next(e for e in events.transitions(read(lower), read(low)) if e["kind"] == "health" and e["device"] == "leaf1")
    assert better["severity"] == "recovered"
