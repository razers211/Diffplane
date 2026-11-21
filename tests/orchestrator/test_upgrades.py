"""Upgrade jobs against a real PostgreSQL and a simulated switch that answers as leaf1
did on the console: plan, approval, one switch at a time, the M-LAG peer check."""

import asyncio
import re
import time

import pytest

from orchestrator import db, switch_cli
from orchestrator.deploys import Verifier, VerifySettings, get_deploy, rolling_out
from orchestrator.images import ImageStore
from orchestrator.sftp import Accounts
from orchestrator.upgrades import UpgradeError, UpgradeJobs, UpgradeSettings
from tests.orchestrator.test_deploys import FakePrometheus, two_active_leaves
from tests import pg

pg.require()

OLD_CC, OLD_PAT = "CE6800-8800_V300R023C00SPC500.cc", "CE6800-8800_V300R023SPH120.PAT"
RUN_CC, RUN_PAT = "CE6800_V300R024C00SPC100.cc", "CE6800_V300R024SPH050.PAT"
NEW_CC, NEW_PAT = "CE6800_V300R025C00SPC500.cc", "CE6800_V300R025SPH120.PAT"

VERSION = """Huawei Versatile Routing Platform Software
YunShan OS, Version 1.25.0.1 (CE6881H V300R025C00SPC500)
Copyright (C) 2021-2025 Huawei Technologies Co., Ltd.
HUAWEI CE6881H-48S6CQ uptime is 4 days, 5 hours, 53 minutes
Patch Version: V300R025SPH120

CE6881H-48S6CQ(Master) 1 : uptime is  4 days, 5 hours, 52 minutes
"""
STARTUP = """MainBoard:
  Configured startup system software:        flash:/CE6800-8800-9800_V300R025C00SPC500.cc
  Startup system software:                   flash:/CE6800-8800-9800_V300R025C00SPC500.cc
  Next startup system software:              flash:/CE6800-8800-9800_V300R025C00SPC500.cc
  Startup saved-configuration file:          flash:/config.cfg
  Next startup saved-configuration file:     flash:/config.cfg
  Startup paf file:                          default
  Next startup paf file:                     default
  Startup patch package:                     flash:/CE6800-8800-9800_V300R025SPH120.PAT
  Next startup patch package:                flash:/CE6800-8800-9800_V300R025SPH120.PAT
  Startup feature software:                  NULL
  Next startup feature software:             NULL
"""
DIR = """Directory of flash:/

  Idx  Attr     Size(Byte)  Date        Time       FileName
    0  drwx              -  Jun 11 2024 16:44:21   $_autobackup
    7  -rw-    589,355,684  Oct 18 2026 14:05:13   CE6800-8800-9800_V300R025C00SPC500.cc
    8  -rw-     34,340,471  Oct 18 2026 14:01:44   CE6800-8800-9800_V300R025SPH120.PAT
    9  -rw-    421,445,404  Jun 11 2024 16:40:47   CE6800-8800_V300R023C00SPC500.cc
   13  -rw-         11,762  Oct 22 2026 17:10:23   config.cfg
   21  drwx              -  Jun 11 2024 16:44:00   pki

2,888,360 KB total (1,798,900 KB free)
"""


def test_parsers_read_the_lab_switchs_output():
    assert switch_cli.version(VERSION) == ("V300R025C00SPC500", "V300R025SPH120")
    assert switch_cli.startup_files(STARTUP) == {
        "system": "CE6800-8800-9800_V300R025C00SPC500.cc", "next system": "CE6800-8800-9800_V300R025C00SPC500.cc",
        "patch": "CE6800-8800-9800_V300R025SPH120.PAT", "next patch": "CE6800-8800-9800_V300R025SPH120.PAT"}
    files, free = switch_cli.flash(DIR)
    assert files == {"CE6800-8800-9800_V300R025C00SPC500.cc": 589355684, "CE6800-8800-9800_V300R025SPH120.PAT": 34340471,
                     "CE6800-8800_V300R023C00SPC500.cc": 421445404, "config.cfg": 11762}
    assert free == 1798900 * 1024


class Switch:
    """One switch as the console showed it: R024 running, R023 left on flash."""

    def __init__(self, accounts, store):
        self.accounts, self.store = accounts, store
        self.system, self.patch = RUN_CC, RUN_PAT
        self.next = {"system": RUN_CC, "patch": RUN_PAT}
        self.files = {OLD_CC: 400_000_000, OLD_PAT: 2_000_000, RUN_CC: 500_000_000, RUN_PAT: 30_000_000, "config.cfg": 11762}
        self.down = 0   # polls left without SSH
        self.system_view = False
        self.integrity = []

    @staticmethod
    def release(name):
        return re.search(r"_(V\w+)\.", name).group(1)

    def answer(self, step):
        command = step.command
        if command == "display version":
            return (f"YunShan OS, Version 1.24.0.1 (CE6881H {self.release(self.system)})\n"
                    + (f"Patch Version: {self.release(self.patch)}\n" if self.patch else ""))
        if command == "display startup":
            return (f"  Startup system software:   flash:/{self.system}\n"
                    f"  Next startup system software:   flash:/{self.next['system']}\n"
                    f"  Startup patch package:   flash:/{self.patch}\n"
                    f"  Next startup patch package:   flash:/{self.next['patch']}\n")
        if command.startswith("dir flash:/") and command != "dir flash:/":   # one file (rack2-leaf1)
            name = command.removeprefix("dir flash:/")
            return (f"Directory of flash:/\n\n  Idx  Attr     Size(Byte)  Date        Time       FileName\n"
                    f"    0  -rw-    {self.files[name]:,}  Oct 25 2026 23:50:20   {name}\n\n"
                    "2,888,124 KB total (1,857,496 KB free)\n")
        if command == "dir flash:/":
            return "".join(f"  {i}  -rw-  {s:,}  Oct 18 2026 14:05:13   {n}\n" for i, (n, s) in enumerate(self.files.items())) \
                + "2,888,360 KB total (1,000,000 KB free)\n"
        if command.startswith("delete /unreserved flash:/"):
            name = command.rsplit("/", 1)[1]
            assert step.questions == ((f"Warning: The file flash:/{name} cannot be recycled. Continue? [Y/N]:", "Y"),)
            del self.files[name]
            return f"Info: Unreserved deleting file flash:/{name}...Done."
        if command in ("system-view", "return"):
            self.system_view = command == "system-view"
            return "Enter system view, return user view with return command." if self.system_view else ""
        if command.startswith("sftp client-transfile get"):
            assert self.system_view, "Error: Unrecognized command found at '^' position."
            words = command.split()
            login, password, name = (words[words.index(w) + 1] for w in ("username", "password", "sourcefile"))
            assert step.secret and words[words.index("destination") + 1] == f"flash:/{name}" and name not in self.files
            assert self.accounts.check(login, password) == [name]   # a one-time account for this file only
            self.files[name] = self.sizes[name]
            return "Downloading file successfully ended.\nFile download is completed in 2 seconds."
        assert not self.system_view, f"{command} sent in system view"
        if command.startswith("check file-digest") and "R023" in self.system:
            return "            ^\nError: Unrecognized command found at '^' position."   # V300R023
        if command.startswith(("check software file-name ", "check patch ")):
            question = ("Warning: Software file verification consumes CPU resources. Continue? [Y/N]:"
                        if command.startswith("check software") else   # V300R023
                        "Warning: Package verification consumes system CPU resources. Continue? [Y/N]:")
            assert "R023" in self.system and step.questions == ((question, "Y"),)
            name = command.rsplit("flash:/", 1)[1]
            assert name in self.files and self.files[name] == self.sizes[name]
            self.integrity.append(name)
            return ("Info: Operating, please wait for a moment, 100% completed.\n"
                    f"Info: The integrity check for the system software of the {self.release(name)} version succeeds.")
        if command.startswith("check file-digest"):
            _, _, _, _, sha256, path = command.split()
            assert path.removeprefix("flash:/") in self.files and sha256 == self.digests[path.removeprefix("flash:/")]
            return f"Info: Verification completed. The digest value of {path} is the same as the input digest value."
        if command.startswith("startup "):
            kind = "system" if "system-software" in command else "patch"
            assert command.endswith(" all") == (kind == "patch"), command
            self.next[kind] = command.split()[2].removeprefix("flash:/")
            return ""
        if command == "reboot":
            assert [a for _, a in step.questions] == ["Y", "Y"]
            self.system, self.patch, self.down = self.next["system"], self.next["patch"], 2
            raise ConnectionError("session closed")
        raise AssertionError(f"unexpected command {command}")


class FakeAnnet:
    def __init__(self, switches):
        self.switches = switches
        self.sent: list[tuple[str, str]] = []

    async def commands(self, device, ip, steps):
        outputs = []
        for step in steps:
            self.sent.append((device, step.command))
            output = self.switches[device].answer(step)
            switch_cli.check_output(step, output)
            outputs.append(output)
        return outputs

    async def diff(self, device):
        return ""


class PeerPrometheus(FakePrometheus):
    def __init__(self, heartbeat=1):
        super().__init__()
        self.heartbeat = heartbeat

    async def query(self, expr):
        value = self.heartbeat if "heartbeat" in expr else 1
        return [({"device": "x", "interface": "Eth-Trunk0"}, time.time(), value)]


@pytest.fixture
def env(database_url, tmp_path, monkeypatch):
    monkeypatch.setenv("SFTP_ADDRESS", "198.51.100.2")
    monkeypatch.setenv("SFTP_PORT", "2222")
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    store = ImageStore(sessions, tmp_path / "images")
    store.directory.mkdir()

    async def body(data):
        yield data

    made = {n: asyncio.run(store.add(n, body(n.encode() * 1000), None, "root")) for n in (NEW_CC, NEW_PAT)}
    accounts = Accounts(tmp_path)
    switches = {d: Switch(accounts, store) for d in ("leaf1", "leaf2")}
    for switch in switches.values():
        switch.sizes = {n: i["size"] for n, i in made.items()}
        switch.digests = {n: i["sha256"] for n, i in made.items()}

    def make(prometheus=None):
        annet = FakeAnnet(switches)
        nb = two_active_leaves()
        verifier = Verifier(annet, prometheus or PeerPrometheus(), nb, VerifySettings(collector_timeout=0.3, poll_interval=0.01))
        from orchestrator.deploys import DeployJobs

        jobs = UpgradeJobs(DeployJobs(sessions, annet, nb, verifier), store, accounts,
                           UpgradeSettings(down_timeout=1, up_timeout=1, verify_timeout=1, poll=0.01))

        async def reachable(ip):
            switch = switches["leaf1" if ip == "172.24.0.11" else "leaf2"]
            if switch.down:
                switch.down -= 1
                return False
            return True

        jobs._reachable = reachable
        return jobs, annet

    return make, made, switches, accounts


async def requested(jobs, made, devices=("leaf1", "leaf2")):
    job_id = await jobs.request("alice", list(devices), system=made[NEW_CC]["id"], patch=made[NEW_PAT]["id"])
    await jobs.wait()
    return job_id


def test_the_plan_deletes_only_images_neither_running_nor_next_and_reads_only(env):
    make, made, switches, _ = env
    jobs, annet = make()

    async def scenario():
        return await get_deploy(jobs.sessions, await requested(jobs, made), "upgrade")

    job = asyncio.run(scenario())
    assert job["status"] == "diffed" and [s["status"] for s in job["steps"]] == ["diffed", "diffed"]
    plan = job["steps"][0]["patch"].splitlines()
    assert plan[0].startswith("# runs V300R024C00SPC100 with patch V300R024SPH050")
    assert plan[1:3] == [f"delete /unreserved flash:/{OLD_CC}   # 400.0 MB", f"delete /unreserved flash:/{OLD_PAT}   # 2.0 MB"]
    assert any(f"sourcefile {NEW_CC} destination flash:/{NEW_CC}" in line and "<one-time>" in line for line in plan)
    assert plan[-3:-1] == [f"startup patch flash:/{NEW_PAT} all", "reboot (save the configuration: Y, reboot: Y)"]
    assert {c for _, c in annet.sent} == {"display version", "display startup", "dir flash:/"}


def test_approved_upgrade_goes_switch_by_switch_and_verifies(env):
    make, made, switches, accounts = env
    jobs, annet = make()

    async def scenario():
        job_id = await requested(jobs, made)
        await jobs.approve(job_id, "bob")
        await jobs.wait()
        return await get_deploy(jobs.sessions, job_id, "upgrade")

    job = asyncio.run(scenario())
    assert job["status"] == "verified", job["log"]
    assert jobs.progress(job["id"]) is None   # no download running any more
    for switch in switches.values():
        assert (switch.system, switch.patch) == (NEW_CC, NEW_PAT)
        assert set(switch.files) == {RUN_CC, RUN_PAT, NEW_CC, NEW_PAT, "config.cfg"}   # the previous release stays
    order = [(d, c.split()[0]) for d, c in annet.sent if c.split()[0] in ("reboot", "sftp")]
    assert order == [("leaf1", "sftp"), ("leaf1", "sftp"), ("leaf1", "reboot"),
                     ("leaf2", "sftp"), ("leaf2", "sftp"), ("leaf2", "reboot")]
    assert accounts.path.read_text().strip() in ("{}", "")   # every one-time account removed
    assert not any("password" in line["message"] for line in job["log"])


def test_an_unhealthy_peer_stops_before_anything_changes(env):
    make, made, switches, _ = env
    jobs, annet = make(PeerPrometheus(heartbeat=0))

    async def scenario():
        job_id = await requested(jobs, made)
        await jobs.approve(job_id, "bob")
        await jobs.wait()
        return await get_deploy(jobs.sessions, job_id, "upgrade")

    job = asyncio.run(scenario())
    assert job["status"] == "failed" and "heartbeat" in job["error"]
    assert [s["status"] for s in job["steps"]] == ["failed", "skipped"]
    assert OLD_CC in switches["leaf1"].files and not any(c.startswith(("delete", "reboot")) for _, c in annet.sent)


def test_an_upgrade_rolling_out_freezes_intent(env):
    make, made, _, _ = env
    jobs, _ = make()

    async def scenario():
        job_id = await requested(jobs, made, ["leaf1"])
        async with jobs.sessions() as session:
            (await session.get(db.Job, job_id)).status = "deploying"
            await session.commit()
        return job_id, await rolling_out(jobs.sessions)

    job_id, running = asyncio.run(scenario())
    assert running == job_id


def test_requests_refused(env, monkeypatch):
    make, made, _, _ = env
    jobs, _ = make()
    with pytest.raises(UpgradeError, match="choose"):
        asyncio.run(jobs.request("alice", ["leaf1"]))
    with pytest.raises(UpgradeError, match="no patch image"):
        asyncio.run(jobs.request("alice", ["leaf1"], patch=made[NEW_CC]["id"]))
    (jobs.images.directory / NEW_PAT).unlink()
    with pytest.raises(UpgradeError, match="upload it again"):
        asyncio.run(jobs.request("alice", ["leaf1"], patch=made[NEW_PAT]["id"]))
    monkeypatch.delenv("SFTP_ADDRESS")
    with pytest.raises(UpgradeError, match="image server address"):
        asyncio.run(jobs.request("alice", ["leaf1"], system=made[NEW_CC]["id"]))


def test_a_switch_on_r023_checks_size_and_package_instead_of_the_digest(env):
    """R023 has no `check file-digest` (an imported switch): the exact size on flash, then
    `check software` / `check patch` with their CPU warning answered."""
    make, made, switches, _ = env
    old = switches["leaf2"]
    old.files[OLD_CC], old.files[OLD_PAT] = old.files.pop(RUN_CC), old.files.pop(RUN_PAT)
    old.system, old.patch = OLD_CC, OLD_PAT
    old.next = {"system": OLD_CC, "patch": OLD_PAT}
    jobs, annet = make()

    async def scenario():
        job_id = await requested(jobs, made)
        plan = (await get_deploy(jobs.sessions, job_id, "upgrade"))["steps"][1]["patch"]
        await jobs.approve(job_id, "bob")
        await jobs.wait()
        return plan, await get_deploy(jobs.sessions, job_id, "upgrade")

    plan, job = asyncio.run(scenario())
    assert job["status"] == "verified", job["log"]
    assert f"dir flash:/{NEW_CC}" in plan and f"check software file-name flash:/{NEW_CC}" in plan
    assert f"check patch flash:/{NEW_PAT}" in plan and "file-digest" not in plan
    assert old.integrity == [NEW_CC, NEW_PAT] and switches["leaf1"].integrity == []
    assert (old.system, old.patch) == (NEW_CC, NEW_PAT)


def test_an_upgrade_scheduled_for_a_window_waits_then_runs_and_is_rechecked_first(env):
    """an upgrade approved for a window sends nothing before it, then runs at its start;
    a switch whose flash changed since approval fails the recheck and nothing is upgraded."""
    from tests.orchestrator.test_windows import SAT, Clock

    make, made, switches, _ = env

    def scenario(change_flash):
        jobs, annet = make()
        jobs.clock = Clock()

        async def go():
            job_id = await requested(jobs, made)
            await jobs.approve(job_id, "bob", SAT)
            await jobs.tick()
            await jobs.wait()
            waiting = await get_deploy(jobs.sessions, job_id, "upgrade")
            sent_before = [c for _, c in annet.sent if not c.startswith(("display", "dir"))]
            if change_flash:
                switches["leaf2"].files["CE6800_EXTRA.cc"] = 1_000   # an image the approved plan does not delete
            jobs.clock.at = SAT[0]
            await jobs.tick()
            await jobs.wait()
            return waiting, sent_before, await get_deploy(jobs.sessions, job_id, "upgrade"), annet

        return asyncio.run(go())

    _, _, refused, annet = scenario(change_flash=True)   # first: the switches are not upgraded yet
    assert refused["status"] == "failed" and "leaf2" in refused["error"] and "nothing was upgraded" in refused["error"]
    assert not any(c.split()[0] in ("sftp", "reboot", "delete", "startup") for _, c in annet.sent)

    del switches["leaf2"].files["CE6800_EXTRA.cc"]
    waiting, sent_before, done, annet = scenario(change_flash=False)
    assert waiting["status"] == "scheduled" and sent_before == []
    assert done["status"] == "verified", done["log"]
    assert any(row["message"] == "window opened" for row in done["log"])
