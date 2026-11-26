"""Deploy jobs against a real PostgreSQL: approval, rollout order, verification, stop on failure.

The example fabric with leaf2 made active (a management address) is the fake second
device: The deploy rules ask that a forced failure on the first device stops the
rollout before the second.
"""

import asyncio
import time
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from orchestrator import db, deploys
from orchestrator.annet import AnnetError
from orchestrator.routing_import import ImportReport
from orchestrator.routing_intent import RoutingStore
from orchestrator.deploys import DeployError, DeployJobs, Verifier, VerifySettings, get_deploy, needs_weak_algorithms, rollout_waves
from tests.helpers import seed
from seed.model import parse
from tests.seed.fake_netbox import FakeNetBox
from tests import pg

pg.require()
ROOT = Path(__file__).resolve().parents[2]
PATCH = "interface LoopBack0\n  description test\n  quit\n"


def two_active_leaves() -> FakeNetBox:
    data = yaml.safe_load((ROOT / "tests" / "fixtures" / "example-small.yaml").read_text())
    nb = FakeNetBox()
    asyncio.run(seed(nb, parse(data), active={"leaf1": "172.24.0.11/24", "leaf2": "172.24.0.105/24"}))
    return nb


class FakeAnnet:
    """Patches until deployed; then the diff is empty unless told otherwise."""

    def __init__(self, patches=None, diff_after=None, deploy_error=None):
        self.patches = patches if patches is not None else {"leaf1": PATCH, "leaf2": PATCH}
        self.diff_after = diff_after or {}
        self.deploy_error = deploy_error or {}
        self.deployed: list[str] = []
        self.sent: list[tuple[str, str, list[str]]] = []   # switch commands outside a patch

    async def commands(self, device, ip, steps):
        self.sent.append((device, ip, [s.command for s in steps]))
        return ["" for _ in steps]

    async def patch(self, device, generators=None):
        return "" if device in self.deployed else self.patches.get(device, "")

    async def deploy(self, device, generators=None):
        if device in self.deploy_error:
            raise AnnetError(self.deploy_error[device])
        self.deployed.append(device)
        return ""

    async def diff(self, device):
        return self.diff_after.get(device, "")


class FakePrometheus:
    """The collector polls every device right after any deploy, unless frozen."""

    def __init__(self, frozen=(), links=None, links_after=None):
        self.frozen = set(frozen)
        self.links = links or {}
        self.links_after = links_after or {}
        self.deployed = False

    async def device_state(self, device):
        last = time.time() + 1 if device not in self.frozen else time.time() - 3600
        oper = self.links_after.get(device) if self.deployed else self.links.get(device)
        return last, True, oper or {}


def make(database_url, annet=None, prometheus=None, nb=None):
    nb = nb or two_active_leaves()
    annet = annet or FakeAnnet()
    prometheus = prometheus or FakePrometheus()
    original = annet.deploy

    async def deploy(device, generators=None):
        result = await original(device, generators)
        prometheus.deployed = True
        return result

    annet.deploy = deploy
    verifier = Verifier(annet, prometheus, nb, VerifySettings(collector_timeout=0.3, poll_interval=0.05))
    jobs = DeployJobs(db.make_sessionmaker(db.make_engine(database_url, pooled=False)), annet, nb, verifier)
    return jobs, annet


def run(coro):
    return asyncio.run(coro)


async def request_and_wait(jobs, **scope):
    job_id = await jobs.request("alice", **scope)
    await jobs.wait()
    return job_id


async def approve_and_wait(jobs, job_id, user="bob"):
    await jobs.approve(job_id, user)
    await jobs.wait()
    return await get_deploy(jobs.sessions, job_id)


def statuses(job):
    return {s["device"]: s["status"] for s in job["steps"]}


def test_request_shows_patches_and_waits_for_approval(database_url):
    jobs, annet = make(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "diffed" and job["requested_by"] == "alice"
    assert statuses(job) == {"leaf1": "diffed", "leaf2": "diffed"}
    assert job["steps"][0]["patch"] == PATCH
    assert annet.deployed == []   # nothing is sent before approval


def test_approved_rollout_deploys_a_pair_one_leaf_at_a_time_and_verifies(database_url):
    jobs, annet = make(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        job = await approve_and_wait(jobs, job_id)
        async with jobs.sessions() as session:
            actions = (await session.scalars(
                select(db.AuditEvent.action).where(db.AuditEvent.job_id == job_id).order_by(db.AuditEvent.id)
            )).all()
        return job, actions

    job, actions = run(scenario())

    assert annet.deployed == ["leaf1", "leaf2"]
    assert job["status"] == "verified" and job["approved_by"] == "bob"
    # each verified switch saves its config, at its management address
    assert annet.sent == [("leaf1", "172.24.0.11", ["save"]), ("leaf2", "172.24.0.105", ["save"])]
    assert statuses(job) == {"leaf1": "verified", "leaf2": "verified"}
    assert actions == ["deploy.request", "deploy.approve", "deploy.verified"]
    assert any("verified" in line["message"] for line in job["log"])


def test_a_pair_is_imported_once_per_deploy(database_url):
    """A pair's import reads both leaves: one per pair, not one per leaf (imported fabrics)."""
    jobs, _ = make(database_url)

    class Importer:
        def __init__(self, nb):
            self.store, self.ran = RoutingStore(nb), []

        async def run(self, device, user):
            self.ran.append(device)
            return ImportReport(device, at=0)

    jobs.importer = Importer(jobs.netbox)
    run(request_and_wait(jobs, site="example"))

    assert len(jobs.importer.ran) == 1 and jobs.importer.ran[0] in ("leaf1", "leaf2")


def test_the_next_wave_does_not_wait_for_the_save_and_the_job_does(database_url):
    """Save and backup of a verified switch run while the next wave deploys."""
    events = []

    class SlowSave(FakeAnnet):
        async def deploy(self, device, generators=None):
            events.append(f"deploy {device}")
            return await super().deploy(device, generators)

        async def commands(self, device, ip, steps):
            await asyncio.sleep(0.5)
            events.append(f"saved {device}")
            return await super().commands(device, ip, steps)

    jobs, annet = make(database_url, annet=SlowSave())

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert events.index("deploy leaf2") < events.index("saved leaf1")
    assert job["status"] == "verified" and {d for d, _, _ in annet.sent} == {"leaf1", "leaf2"}
    log = [line["message"] for line in job["log"]]
    assert log.index("all devices deployed and verified") > max(i for i, m in enumerate(log) if m == "config saved")


def test_forced_failure_on_the_first_device_stops_the_rollout(database_url):
    """Verification fails on the first device; the second is never deployed."""
    jobs, annet = make(database_url, annet=FakeAnnet(diff_after={"leaf1": "+ something left over\n"}))

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert annet.deployed == ["leaf1"]
    assert job["status"] == "failed" and job["error"].startswith("leaf1: diff is not empty")
    assert statuses(job) == {"leaf1": "failed", "leaf2": "skipped"}


def test_snmp_v2c_installs_weakea_before_the_patch(database_url):
    """Only a patch that turns SNMPv2c on installs WEAKEA first, and the prepare log says so."""
    v2c = PATCH + "snmp-agent sys-info version v2c\n"
    jobs, annet = make(database_url, annet=FakeAnnet(patches={"leaf1": v2c, "leaf2": PATCH}))

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert job["status"] == "verified"
    assert annet.sent == [("leaf1", "172.24.0.11", ["install feature-software WEAKEA"]),
                          ("leaf1", "172.24.0.11", ["save"]), ("leaf2", "172.24.0.105", ["save"])]
    assert any("install feature-software WEAKEA` first" in line["message"] for line in job["log"])


def test_only_turning_v2c_on_needs_weak_algorithms():
    assert needs_weak_algorithms("snmp-agent sys-info version v2c v3\n")
    assert not needs_weak_algorithms("snmp-agent sys-info version v2c disable\n")
    assert not needs_weak_algorithms("snmp-agent sys-info version v3\nundo snmp-agent sys-info version v2c\n")


def test_waves_keep_pairs_apart_and_take_half_the_fabric():
    """An imported fabric's shape: a spine pair, two leaf pairs and a lone switch."""
    pairs = [("gw-spine1", "gw-spine2"), ("rack1-leaf1", "rack1-leaf2"), ("rack2-leaf1", "rack2-leaf2")]
    names = sorted([n for p in pairs for n in p] + ["sw-oob1"])
    ids = {n: i for i, n in enumerate(names)}
    peer = {a: b for p in pairs for a, b in (p, p[::-1])}
    devices = [{"id": ids[n], "name": n, "site": {"slug": "site-a"},
                "role": {"slug": "spine" if n.startswith("gw-") else "leaf"},
                "custom_fields": {"mlag_peer": {"id": ids[peer[n]]} if n in peer else None}} for n in names]

    assert rollout_waves(names, devices) == [
        ["gw-spine1", "rack1-leaf1", "rack2-leaf1"], ["gw-spine2", "rack1-leaf2", "rack2-leaf2"], ["sw-oob1"]]
    # two single spines never go together, whatever room the wave has
    singles = [dict(d, custom_fields={}) for d in devices]
    assert not any({"gw-spine1", "gw-spine2"} <= set(w) for w in rollout_waves(names, singles))


def test_a_failure_finishes_its_wave_and_starts_no_other(database_url, monkeypatch):
    monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "example-central-spines")
    leaves = ["s-leaf1", "s-leaf2", "s-leaf3", "s-leaf4"]   # two pairs: waves leaf1+3, leaf2+4
    nb = FakeNetBox()
    data = yaml.safe_load((ROOT / "seed" / "fabrics" / "example-central-spines.yaml").read_text())
    asyncio.run(seed(nb, parse(data), active={n: f"172.24.1.{i + 1}/24" for i, n in enumerate(leaves)}))
    jobs, annet = make(database_url, nb=nb, annet=FakeAnnet(patches={n: PATCH for n in leaves},
                                                            diff_after={"s-leaf1": "+ left over\n"}))

    async def scenario():
        job_id = await request_and_wait(jobs, site="example-central-spines")
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert sorted(annet.deployed) == ["s-leaf1", "s-leaf3"]
    assert statuses(job) == {"s-leaf1": "failed", "s-leaf2": "skipped", "s-leaf3": "verified",
                             "s-leaf4": "skipped"}
    assert job["status"] == "failed" and job["error"].startswith("s-leaf1: diff is not empty")


def test_a_verified_deploy_clears_drift(database_url):
    """drift reads each device's latest diff; a verified deploy records an empty one."""
    from orchestrator.drift import drift_report
    from orchestrator.jobs import latest_snapshot

    jobs, annet = make(database_url)

    async def scenario():
        async with jobs.sessions() as session:   # the drift run before the deploy saw a change
            drift = db.Job(kind="drift", status="done", requested_by="scheduler")
            drift.snapshots = [db.DiffSnapshot(device="leaf1", status="ok", has_changes=True, diff="+ x\n")]
            session.add(drift)
            await session.commit()
        job_id = await request_and_wait(jobs, devices=["leaf1"])
        await approve_and_wait(jobs, job_id)
        report = await drift_report(jobs, [{"name": "leaf1", "site": {"slug": "example"}}])
        return job_id, report[0], await latest_snapshot(jobs.sessions, "leaf1")

    job_id, row, snap = run(scenario())

    assert row["state"] == "in_sync" and row["job_id"] == job_id and row["job_kind"] == "deploy"
    assert snap["has_changes"] is False and snap["job_kind"] == "deploy"


def test_a_failed_verification_records_no_diff(database_url):
    from orchestrator.jobs import latest_snapshot

    jobs, _ = make(database_url, annet=FakeAnnet(diff_after={"leaf1": "+ something left over\n"}))

    async def scenario():
        job_id = await request_and_wait(jobs, devices=["leaf1"])
        await approve_and_wait(jobs, job_id)
        return await latest_snapshot(jobs.sessions, "leaf1")

    assert run(scenario()) is None


def test_deploy_error_stops_the_rollout(database_url):
    jobs, annet = make(database_url, annet=FakeAnnet(deploy_error={"leaf1": "annet deploy failed: rejected"}))

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert annet.deployed == [] and statuses(job) == {"leaf1": "failed", "leaf2": "skipped"}


def test_collector_must_poll_after_the_deploy(database_url):
    jobs, _ = make(database_url, prometheus=FakePrometheus(frozen={"leaf1"}))

    async def scenario():
        job_id = await request_and_wait(jobs, devices=["leaf1"])
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "collector has not polled" in job["steps"][0]["error"]


def test_a_fabric_link_lost_by_the_deploy_fails_it(database_url):
    prometheus = FakePrometheus(links={"leaf1": {"100GE1/0/1": True}},
                                links_after={"leaf1": {"100GE1/0/1": False}})
    jobs, _ = make(database_url, prometheus=prometheus)

    async def scenario():
        job_id = await request_and_wait(jobs, devices=["leaf1"])
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert "fabric links down after the deploy: 100GE1/0/1" in job["steps"][0]["error"]


def test_patch_changed_since_approval_is_not_deployed(database_url):
    annet = FakeAnnet()
    jobs, _ = make(database_url, annet=annet)

    async def scenario():
        job_id = await request_and_wait(jobs, devices=["leaf1"])
        annet.patches["leaf1"] = PATCH + "interface LoopBack1\n  description changed\n  quit\n"
        return await approve_and_wait(jobs, job_id)

    job = run(scenario())

    assert annet.deployed == [] and "patch changed since it was approved" in job["steps"][0]["error"]


def test_nothing_to_deploy(database_url):
    jobs, _ = make(database_url, annet=FakeAnnet(patches={}))

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        job = await get_deploy(jobs.sessions, job_id)
        with pytest.raises(DeployError, match="not awaiting approval"):
            await jobs.approve(job_id, "bob")
        return job

    job = run(scenario())

    assert job["status"] == "unchanged"
    assert statuses(job) == {"leaf1": "unchanged", "leaf2": "unchanged"}


def test_only_allowed_sites(database_url, monkeypatch):
    monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "prod-east")
    jobs, _ = make(database_url)

    with pytest.raises(DeployError, match="only allowed to fabrics"):
        run(jobs.request("alice", site="example"))


def test_deploys_to_arista_switches_are_refused_until_they_are_built(database_url, monkeypatch):
    nb = FakeNetBox()
    asyncio.run(seed(nb, parse(yaml.safe_load((ROOT / "tests" / "fixtures" / "example-arista.yaml").read_text())),
                     active={"eos-leaf1": "172.24.5.11/24"}))
    monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "eos")
    jobs, _ = make(database_url, nb=nb)

    with pytest.raises(DeployError, match="Arista switches are not built yet: eos-leaf1"):
        run(jobs.request("alice", site="eos"))


def test_a_fabric_turned_off_after_preparing_cannot_be_approved(database_url, monkeypatch):
    """deploys are allowed per fabric from the Fabric page; approval checks again."""
    monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "example")
    jobs, _ = make(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, devices=["leaf1"])
        monkeypatch.setenv("DEPLOY_ALLOWED_SITES", "")
        with pytest.raises(DeployError, match="only allowed to fabrics"):
            await jobs.approve(job_id, "bob")

    run(scenario())


def test_planned_devices_cannot_be_deployed(database_url):
    jobs, _ = make(database_url)

    with pytest.raises(DeployError, match="spine1"):
        run(jobs.request("alice", devices=["spine1"]))


def test_one_deploy_at_a_time(database_url):
    jobs, _ = make(database_url)

    async def scenario():
        first = await request_and_wait(jobs, devices=["leaf1"])
        second = await request_and_wait(jobs, devices=["leaf2"])
        async with jobs.sessions() as session:
            job = await session.get(db.Job, first)
            job.status = "deploying"   # as if the first rollout were still running
            await session.commit()
        with pytest.raises(DeployError, match="one rollout at a time"):
            await jobs.approve(second, "bob")

    run(scenario())


def test_restart_during_a_deploy_fails_the_job_loudly(database_url):
    jobs, _ = make(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        async with jobs.sessions() as session:
            job = await session.get(db.Job, job_id)
            job.status = "deploying"
            job.steps[0].status = "deploying"
            await session.commit()
        await jobs.recover()
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed"
    assert statuses(job) == {"leaf1": "failed", "leaf2": "skipped"}
    assert "may be partly configured" in job["steps"][0]["error"]


# -- HTTP ----------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator.app import app, get_deploys  # noqa: E402


class FakeDeploys:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def request(self, user, site=None, devices=None, generators=None):
        if self.error:
            raise self.error
        self.calls.append(("request", user, site, devices, generators))
        return 5

    async def approve(self, job_id, user, window=None):
        if self.error:
            raise self.error
        self.calls.append(("approve", job_id, user) + ((window,) if window else ()))


@pytest.fixture
def http():
    yield
    app.dependency_overrides.pop(get_deploys, None)


def test_http_request_records_the_logged_in_user(http):
    fake = FakeDeploys()
    app.dependency_overrides[get_deploys] = lambda: fake

    response = TestClient(app).post("/deploy-jobs", json={"site": "example", "generators": ["Snmp"]})

    assert response.status_code == 202 and response.json() == {"id": 5}
    assert fake.calls == [("request", "tester", "example", None, ["Snmp"])]


def test_http_approve_records_the_approver_and_maps_conflicts(http):
    fake = FakeDeploys()
    app.dependency_overrides[get_deploys] = lambda: fake

    assert TestClient(app).post("/deploy-jobs/5/approve").status_code == 200
    assert fake.calls == [("approve", 5, "tester")]
    window = {"window_start": "2026-10-31T02:00:00+01:00", "window_end": "2026-10-31T05:00:00+01:00"}
    assert TestClient(app).post("/deploy-jobs/5/approve", json=window).status_code == 200
    start, end = fake.calls[-1][3]
    assert start.isoformat() == "2026-10-31T02:00:00+01:00" and end.hour == 5
    half = {"window_start": "2026-10-31T02:00:00+01:00"}
    assert TestClient(app).post("/deploy-jobs/5/approve", json=half).status_code == 400

    app.dependency_overrides[get_deploys] = lambda: FakeDeploys(DeployError("one deploy at a time"))
    assert TestClient(app).post("/deploy-jobs/5/approve").status_code == 409
    app.dependency_overrides[get_deploys] = lambda: FakeDeploys(DeployError("only allowed to fabrics"))
    assert TestClient(app).post("/deploy-jobs", json={"site": "prod"}).status_code == 400


# -- queue ---------------------------------------------------------------------------

def test_a_request_during_a_rollout_is_queued_then_prepared_after_it(database_url):
    jobs, annet = make(database_url)

    async def scenario():
        first = await request_and_wait(jobs, devices=["leaf1"])
        await jobs.approve(first, "bob")            # rollout starts in the background
        second = await jobs.request("carol", devices=["leaf2"])
        queued = await get_deploy(jobs.sessions, second)
        with pytest.raises(DeployError, match="queued behind a running deploy"):
            await jobs.approve(second, "bob")
        await jobs.wait()
        return queued, await get_deploy(jobs.sessions, first), await get_deploy(jobs.sessions, second)

    queued, first, second = run(scenario())

    assert queued["status"] == "queued" and statuses(queued) == {"leaf2": "pending"}
    assert any("queued" in line["message"] for line in queued["log"])
    assert first["status"] == "verified"
    assert second["status"] == "diffed" and statuses(second) == {"leaf2": "diffed"}
    assert annet.deployed == ["leaf1"]   # the queued job is prepared, not deployed, until approved


def test_queued_jobs_are_prepared_in_request_order(database_url):
    jobs, _ = make(database_url)
    order = []
    original = jobs._prepare

    async def recording(job_id):
        order.append(job_id)
        await original(job_id)

    jobs._prepare = recording

    async def scenario():
        first = await request_and_wait(jobs, devices=["leaf1"])
        order.clear()
        await jobs.approve(first, "bob")
        a = await jobs.request("carol", devices=["leaf2"])
        b = await jobs.request("dave", devices=["leaf1"])
        await jobs.wait()
        return a, b

    a, b = run(scenario())

    assert order == [a, b]


def test_queued_jobs_survive_a_restart_and_are_prepared(database_url):
    jobs, _ = make(database_url)

    async def scenario():
        async with jobs.sessions() as session:
            job = db.Job(kind="deploy", status="queued", requested_by="carol")
            job.steps = [db.DeployStep(device="leaf1", position=0, status="pending")]
            session.add(job)
            await session.commit()
            job_id = job.id
        await jobs.recover()
        await jobs.wait()
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "diffed"


def test_pre_deploy_check_errors_block_approval_and_warnings_do_not(database_url):
    """the checks run when patches are computed and again at approval."""
    jobs, annet = make(database_url)
    verdict = {"blocking": True, "findings": [{"severity": "error", "check": "duplicate-address", "objects": [],
                                               "text": "segment web gateway 10.100.0.1/24 overlaps service FW"}]}

    async def checker(site):
        return {"fabric": site, **verdict}
    jobs.checker = checker

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        prepared = await get_deploy(jobs.sessions, job_id)
        with pytest.raises(DeployError, match="overlaps service FW"):
            await jobs.approve(job_id, "bob")
        verdict.update(blocking=False, findings=[{"severity": "warning", "check": "unattached", "objects": [],
                                                  "text": "segment db is routed but attached to no port"}])
        done = await approve_and_wait(jobs, job_id)
        return prepared, done

    prepared, done = run(scenario())
    assert prepared["checks"]["blocking"] is True and prepared["checks"]["fabrics"][0]["fabric"] == "example"
    assert done["status"] == "verified" and done["checks"]["blocking"] is False   # checked again at approval


def test_checks_that_cannot_run_block_approval(database_url):
    jobs, _ = make(database_url)

    async def broken(site):
        raise RuntimeError("NetBox did not answer")
    jobs.checker = broken

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        with pytest.raises(DeployError, match="could not run"):
            await jobs.approve(job_id, "bob")
        return await get_deploy(jobs.sessions, job_id)

    assert run(scenario())["status"] == "diffed"   # still waiting; nothing deployed


def test_no_fabric_receives_deploys_until_one_is_allowed(monkeypatch):
    monkeypatch.delenv("DEPLOY_ALLOWED_SITES", raising=False)
    assert deploys.allowed_sites() == set()
