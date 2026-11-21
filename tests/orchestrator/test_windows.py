"""Change windows against a real PostgreSQL: a deploy or upgrade approved for a window waits,
starts at the window's start (or late inside it), is rechecked whole first, starts no wave after the
window's end, and can be cancelled before or during its rollout."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator import db
from orchestrator.deploys import DeployError, get_deploy, rolling_out, window_problem
from tests.orchestrator.test_deploys import FakeAnnet, PATCH, make, request_and_wait, run, statuses
from tests import pg

pg.require()

T0 = datetime(2026, 10, 28, 15, 40, tzinfo=timezone.utc)
SAT = (T0 + timedelta(days=2, hours=10, minutes=20), T0 + timedelta(days=2, hours=13, minutes=20))


class Clock:
    def __init__(self, at=T0):
        self.at = at

    def __call__(self):
        return self.at


def scheduled(database_url, annet=None):
    jobs, annet = make(database_url, annet=annet)
    jobs.clock = Clock()
    jobs.reapprove_timeout = 0.3   # an approver's 15 minutes
    return jobs, annet


async def tick_and_wait(jobs):
    await jobs.tick()
    await jobs.wait()


def test_window_problems():
    assert window_problem(*SAT, T0) is None
    assert "after it starts" in window_problem(SAT[1], SAT[0], T0)
    assert "already over" in window_problem(T0 - timedelta(hours=2), T0 - timedelta(hours=1), T0)
    assert "7 days" in window_problem(T0 + timedelta(days=8), T0 + timedelta(days=8, hours=1), T0)
    assert "time zone" in window_problem(SAT[0].replace(tzinfo=None), SAT[1], T0)


def test_a_scheduled_deploy_waits_unfrozen_then_starts_at_the_window(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        await tick_and_wait(jobs)
        waiting = await get_deploy(jobs.sessions, job_id)
        frozen = await rolling_out(jobs.sessions)
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return waiting, frozen, await get_deploy(jobs.sessions, job_id)

    waiting, frozen, done = run(scenario())

    assert waiting["status"] == "scheduled" and waiting["window_start"] == SAT[0].isoformat()
    assert frozen is None   # intent is not frozen while the window waits
    assert done["status"] == "verified" and sorted(annet.deployed) == ["leaf1", "leaf2"]
    assert any("window opened" == row["message"] for row in done["log"])


def test_a_late_start_inside_the_window_says_so(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        jobs.clock.at = SAT[0] + timedelta(minutes=7)   # the platform was down at the start
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "verified"
    assert any("7 min late" in row["message"] for row in job["log"])


def test_a_window_missed_entirely_fails_and_sends_nothing(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        jobs.clock.at = SAT[1] + timedelta(minutes=1)
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "ended before" in job["error"]
    assert annet.deployed == [] and set(statuses(job).values()) == {"skipped"}


def test_a_patch_changed_since_approval_deploys_nothing_at_the_start(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        annet.patches["leaf2"] = PATCH + "interface LoopBack1\n  description new\n  quit\n"
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "leaf2" in job["error"] and "nothing was deployed" in job["error"]
    assert "no approver approved it" in job["error"]   # any difference of live intent needs an approver
    assert annet.deployed == []   # leaf1 matched, but nothing goes out unless every switch does


def test_cancel_before_the_window(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        await jobs.cancel(job_id, "alice")
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        with pytest.raises(DeployError, match="nothing to cancel"):
            await jobs.cancel(job_id, "alice")
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "cancelled" and annet.deployed == []


def test_cancel_during_the_rollout_finishes_the_wave_and_starts_no_other(database_url):
    jobs, annet = scheduled(database_url)
    original = annet.deploy

    async def deploy_and_cancel(device, generators=None):
        result = await original(device, generators)
        if device == "leaf1":
            jobs._cancelled[job_ids[0]] = "alice"   # as POST /cancel during the first wave
        return result

    annet.deploy = deploy_and_cancel
    job_ids = []

    async def scenario():
        job_ids.append(await request_and_wait(jobs, site="example"))
        await jobs.approve(job_ids[0], "bob", SAT)
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_ids[0])

    job = run(scenario())

    assert job["status"] == "cancelled" and annet.deployed == ["leaf1"]
    assert statuses(job) == {"leaf1": "verified", "leaf2": "skipped"}


def test_no_wave_starts_after_the_window_end(database_url):
    jobs, annet = scheduled(database_url)
    original = annet.deploy

    async def deploy_slowly(device, generators=None):
        result = await original(device, generators)
        jobs.clock.at = SAT[1]   # the first wave took until the window's end
        return result

    annet.deploy = deploy_slowly

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "stopped" and "window ended" in job["error"]
    assert statuses(job) == {"leaf1": "verified", "leaf2": "skipped"}


def test_a_restart_keeps_the_window_and_an_unstarted_start_goes_back_to_waiting(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        await jobs.recover()   # restart while it waits
        kept = (await get_deploy(jobs.sessions, job_id))["status"]
        async with jobs.sessions() as session:   # restart right after the start, before any switch
            job = await session.get(db.Job, job_id)
            job.status = "approved"
            await session.commit()
        await jobs.recover()
        back = (await get_deploy(jobs.sessions, job_id))["status"]
        jobs.clock.at = SAT[0] + timedelta(minutes=3)
        await tick_and_wait(jobs)
        return kept, back, await get_deploy(jobs.sessions, job_id)

    kept, back, job = run(scenario())

    assert kept == "scheduled" and back == "scheduled"
    assert job["status"] == "verified"


def test_an_open_window_waits_for_another_rollout(database_url):
    jobs, annet = scheduled(database_url)

    async def scenario():
        first = await request_and_wait(jobs, devices=["leaf1"])
        second = await request_and_wait(jobs, devices=["leaf2"])
        await jobs.approve(second, "bob", SAT)
        async with jobs.sessions() as session:
            job = await session.get(db.Job, first)
            job.status = "deploying"   # another rollout is running at the window's start
            await session.commit()
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        await tick_and_wait(jobs)   # logged once
        waiting = await get_deploy(jobs.sessions, second)
        async with jobs.sessions() as session:
            job = await session.get(db.Job, first)
            job.status = "verified"
            await session.commit()
        await tick_and_wait(jobs)
        return waiting, await get_deploy(jobs.sessions, second)

    waiting, done = run(scenario())

    assert waiting["status"] == "scheduled"
    assert sum("waiting for job" in row["message"] for row in waiting["log"]) == 1
    assert done["status"] == "verified" and "leaf2" in annet.deployed


def test_a_window_cannot_be_too_far_ahead(database_url):
    jobs, _ = scheduled(database_url)

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        far = (T0 + timedelta(days=8), T0 + timedelta(days=8, hours=3))
        with pytest.raises(DeployError, match="7 days"):
            await jobs.approve(job_id, "bob", far)
        return await get_deploy(jobs.sessions, job_id)

    assert run(scenario())["status"] == "diffed"


# -- a change set's deploy (staged changes) -------------------------------------------------

class FakeDrafts:
    """One change set of fabric example; `apply` changes the live intent (here: the counter)."""

    def __init__(self, refuse=None):
        self.status, self.applied, self.refuse = "open", 0, refuse

    async def get(self, change_set_id):
        return {"id": change_set_id, "site": "example", "status": self.status, "steps": [{"method": "POST"}]}

    async def set_status(self, change_set_id, status, expected):
        from orchestrator.drafts import DraftError
        if self.status not in expected:
            raise DraftError(f"change set {change_set_id} is {self.status}")
        self.status = status

    async def copy(self, change_set_id, fresh=False):
        return "copy"

    async def apply(self, change_set_id):
        from orchestrator.drafts import DraftError
        if self.refuse:
            raise DraftError(self.refuse)
        self.applied += 1


def with_drafts(database_url, refuse=None):
    jobs, annet = scheduled(database_url)
    jobs.drafts = FakeDrafts(refuse)
    preview = {"leaf1": PATCH, "leaf2": PATCH}

    async def current(device):
        return "running config"

    async def draft_patches(site, copy, configs):
        assert copy == "copy" and set(configs) == {"leaf1", "leaf2"}
        return dict(preview)

    annet.current = current
    jobs.draft_patches = draft_patches
    return jobs, annet, preview


def test_a_change_set_is_applied_at_its_window_start_then_deployed(database_url):
    jobs, annet, _ = with_drafts(database_url)

    async def scenario():
        job_id = await jobs.request_draft("alice", 7)
        await jobs.wait()
        prepared = await get_deploy(jobs.sessions, job_id)
        with pytest.raises(DeployError, match="choose its window"):
            await jobs.approve(job_id, "bob")
        await jobs.approve(job_id, "bob", SAT)
        locked = jobs.drafts.status
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return prepared, locked, await get_deploy(jobs.sessions, job_id)

    prepared, locked, job = run(scenario())

    assert prepared["status"] == "diffed" and {s["patch"] for s in prepared["steps"]} == {PATCH}
    assert locked == "scheduled"   # it cannot change once its patches were shown
    assert job["status"] == "verified" and jobs.drafts.applied == 1 and jobs.drafts.status == "applied"
    assert any("applying change set 7" in row["message"] for row in job["log"])


def test_a_change_set_whose_patches_changed_is_not_applied_and_kept(database_url):
    jobs, annet, preview = with_drafts(database_url)

    async def scenario():
        job_id = await jobs.request_draft("alice", 7)
        await jobs.wait()
        await jobs.approve(job_id, "bob", SAT)
        preview["leaf2"] = PATCH + "interface LoopBack1\n  description new\n  quit\n"
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "leaf2" in job["error"] and "nothing was changed" in job["error"]
    assert jobs.drafts.applied == 0 and jobs.drafts.status == "open" and annet.deployed == []


def test_a_change_set_refused_at_the_start_deploys_nothing(database_url):
    jobs, annet, _ = with_drafts(database_url, refuse="step 1 (POST /segments) was refused: VNI 10500 is taken")

    async def scenario():
        job_id = await jobs.request_draft("alice", 7)
        await jobs.wait()
        await jobs.approve(job_id, "bob", SAT)
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "VNI 10500 is taken" in job["error"]
    assert annet.deployed == [] and jobs.drafts.status == "open"


def test_cancelling_a_change_sets_window_gives_the_change_set_back(database_url):
    jobs, _, _ = with_drafts(database_url)

    async def scenario():
        job_id = await jobs.request_draft("alice", 7)
        await jobs.wait()
        await jobs.approve(job_id, "bob", SAT)
        await jobs.cancel(job_id, "alice")
        return await get_deploy(jobs.sessions, job_id)

    assert run(scenario())["status"] == "cancelled" and jobs.drafts.status == "open"


# -- a switch that differs at the start ----------------------------------------

EXTRA = "interface LoopBack1\n  description test\n  quit\n"   # PATCH's command on another interface


def differing(database_url, verified_since=True):
    """A change set approved with PATCH on both leaves; at the start leaf2's patch has one more
    line of the same shape. Live intent has no undeployed change; the change set's lines appear on
    the live patches once it is applied."""
    jobs, annet, preview = with_drafts(database_url)

    async def patch(device, generators=None):
        if device in annet.deployed or not jobs.drafts.applied:
            return ""
        return preview.get(device, "")

    annet.patch = patch

    async def prepare_and_approve():
        job_id = await jobs.request_draft("alice", 7)
        await jobs.wait()
        await jobs.approve(job_id, "bob", SAT)
        preview["leaf2"] = PATCH + EXTRA
        if verified_since:   # e.g. a member added to a trunk, deployed and verified meanwhile
            async with jobs.sessions() as session:
                other = db.Job(kind="deploy", status="verified", site="example", finished_at=db.now())
                other.steps = [db.DeployStep(device="leaf2", position=0, status="verified")]
                session.add(other)
                await session.commit()
        jobs.clock.at = SAT[0]
        return job_id

    return jobs, annet, prepare_and_approve


async def started_and_waiting(jobs, job_id):
    await jobs.tick()
    for _ in range(100):
        if (await get_deploy(jobs.sessions, job_id))["status"] == "reapproval":
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the job never asked for an approver")


def test_a_safe_difference_goes_on_by_itself(database_url):
    jobs, annet, prepare_and_approve = differing(database_url)

    async def scenario():
        job_id = await prepare_and_approve()
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "verified", job["error"]
    assert {s["device"]: s["patch"] for s in job["steps"]}["leaf2"] == PATCH + EXTRA
    assert any("only by additions of the approved kinds" in row["message"] for row in job["log"])


def test_a_difference_without_a_verified_deploy_waits_for_an_approver(database_url):
    jobs, annet, prepare_and_approve = differing(database_url, verified_since=False)
    jobs.reapprove_timeout = 10

    async def scenario():
        job_id = await prepare_and_approve()
        await started_and_waiting(jobs, job_id)
        asked = await get_deploy(jobs.sessions, job_id)
        frozen = await rolling_out(jobs.sessions)
        await jobs.approve(job_id, "carol")
        await jobs.wait()
        return asked, frozen, await get_deploy(jobs.sessions, job_id)

    asked, frozen, job = run(scenario())

    assert "no deploy of it was verified since the approval" in asked["params"]["asked"]
    assert asked["params"]["new_patches"]["leaf2"] == PATCH + EXTRA
    assert frozen == job["id"]   # intent is frozen from the window's start
    assert job["status"] == "verified" and jobs.drafts.status == "applied"
    assert any("new patch approved by carol" == row["message"] for row in job["log"])


def test_no_approver_in_time_deploys_nothing_and_keeps_the_change_set(database_url):
    jobs, annet, prepare_and_approve = differing(database_url, verified_since=False)

    async def scenario():
        job_id = await prepare_and_approve()
        await tick_and_wait(jobs)
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "no approver approved it" in job["error"]
    assert annet.deployed == [] and jobs.drafts.applied == 0 and jobs.drafts.status == "open"


def test_cancel_while_waiting_for_an_approver(database_url):
    jobs, annet, prepare_and_approve = differing(database_url, verified_since=False)
    jobs.reapprove_timeout = 10

    async def scenario():
        job_id = await prepare_and_approve()
        await started_and_waiting(jobs, job_id)
        await jobs.cancel(job_id, "alice")
        await jobs.wait()
        return await get_deploy(jobs.sessions, job_id)

    job = run(scenario())

    assert "cancelled by alice" in job["error"] and annet.deployed == [] and jobs.drafts.status == "open"


# -- window news: the timeline and the alert receiver --------------------------

class FakeEvents:
    def __init__(self):
        self.rows = []

    async def record(self, events):
        self.rows += events


def test_window_news_goes_on_the_timeline_and_to_the_alert_receiver(database_url, monkeypatch):
    from orchestrator import alerting

    alerts = []

    async def send(name, severity, summary, labels=None, minutes=5, env=None):
        alerts.append(labels["state"])

    monkeypatch.setattr(alerting, "send", send)
    jobs, annet = scheduled(database_url)
    jobs.events = FakeEvents()

    async def scenario():
        job_id = await request_and_wait(jobs, site="example")
        await jobs.approve(job_id, "bob", SAT)
        jobs.clock.at = SAT[0] - timedelta(minutes=10)
        await tick_and_wait(jobs)
        await tick_and_wait(jobs)   # reminded once
        jobs.clock.at = SAT[0]
        await tick_and_wait(jobs)
        annet.deployed.clear()
        failed = await request_and_wait(jobs, site="example")   # a second window, missed
        await jobs.approve(failed, "bob", (SAT[0] + timedelta(days=1), SAT[1] + timedelta(days=1)))
        jobs.clock.at = SAT[1] + timedelta(days=1, minutes=1)
        await tick_and_wait(jobs)

    run(scenario())

    states = [e["state"] for e in jobs.events.rows]
    assert states == ["starts soon", "started", "verified", "failed"]
    assert {e["kind"] for e in jobs.events.rows} == {"window"}
    assert alerts == ["starts soon", "verified", "failed"]   # "started" only on the timeline
