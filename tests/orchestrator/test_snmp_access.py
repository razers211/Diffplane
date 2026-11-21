"""SNMPv3 readers and trap targets: the intent in `fabric.snmp`, the write-only passwords and
the password job, against a simulated switch that asks as leaf1 did."""

import asyncio

import pytest

from orchestrator import db, fabric_ntp, redact, snmp_access, snmp_passwords, switch_cli
from orchestrator.deploys import DeployJobs, Verifier, VerifySettings, get_deploy
from orchestrator.segments import SegmentError
from seed.model import load
from tests.collector.test_collector import example_netbox
from tests.helpers import seed
from tests.orchestrator.test_deploys import FakePrometheus, two_active_leaves
from tests.seed.test_loader import EXAMPLE


def run(coro):
    return asyncio.run(coro)


def snmp_context(nb) -> dict:
    return run(nb.find_one("extras/config-contexts", name="fabric-example"))["data"]["fabric"]["snmp"]


def test_readers_and_traps_are_intent_and_owned_grows():
    nb = example_netbox()
    run(fabric_ntp.set_snmp(nb, "example", "hall 2", ""))
    run(snmp_access.set_reader(nb, "example", "nms"))
    run(snmp_access.set_trap(nb, "example", "nms-1", " 192.0.2.10 ", "nms"))
    assert snmp_context(nb) == {"location": "hall 2", "readers": {"nms": {"auth": "sha2-256", "priv": "aes256"}},
                                "traps": {"nms-1": {"address": "192.0.2.10", "reader": "nms"}},
                                "owned": {"users": ["nms"], "traps": ["nms-1"]}}
    run(fabric_ntp.set_snmp(nb, "example", "hall 3", "noc@example.net"))   # the location form keeps the readers
    run(seed(nb, load(EXAMPLE)))   # and so does re-seeding
    assert snmp_context(nb)["readers"] == {"nms": {"auth": "sha2-256", "priv": "aes256"}}
    with pytest.raises(SegmentError, match="send to nms"):
        run(snmp_access.delete_reader(nb, "example", "nms"))
    run(snmp_access.delete_trap(nb, "example", "nms-1"))
    run(snmp_access.delete_reader(nb, "example", "nms"))
    snmp = snmp_context(nb)
    assert "readers" not in snmp and "traps" not in snmp
    assert snmp["owned"] == {"users": ["nms"], "traps": ["nms-1"]}   # so the generator removes them


def test_v2c_off_needs_a_reader_and_bad_values_are_refused():
    nb = example_netbox()
    with pytest.raises(SegmentError, match="before turning v2c off"):
        run(snmp_access.set_v2c(nb, "example", False))
    run(snmp_access.set_reader(nb, "example", "nms", "sha2-512", "aes128"))
    run(snmp_access.set_v2c(nb, "example", False))
    assert snmp_context(nb)["v2c"] is False
    with pytest.raises(SegmentError, match="last reader"):
        run(snmp_access.delete_reader(nb, "example", "nms"))
    for name, auth, priv in (("1nms", "sha2-256", "aes256"), ("nms", "md5", "aes256"), ("nms", "sha2-256", "des56"),
                             ("n" * 33, "sha2-256", "aes256")):
        with pytest.raises(SegmentError):
            run(snmp_access.set_reader(nb, "example", name, auth, priv))
    for address, reader in (("224.0.0.1", "nms"), ("not-an-ip", "nms"), ("192.0.2.1", "nobody")):
        with pytest.raises(SegmentError):
            run(snmp_access.set_trap(nb, "example", "t", address, reader))
    run(snmp_access.set_v2c(nb, "example", True))
    assert "v2c" not in snmp_context(nb)   # on is the default


def test_restore_takes_back_a_rollback_point():
    nb = example_netbox()
    run(snmp_access.set_reader(nb, "example", "nms"))
    then = snmp_access.view(run(snmp_access.get(nb, "example")))
    run(snmp_access.set_reader(nb, "example", "other"))
    run(snmp_access.set_v2c(nb, "example", False))
    run(snmp_access.restore(nb, "example", **then))
    now = run(snmp_access.get(nb, "example"))
    assert snmp_access.view(now) == then and now["owned"]["users"] == ["nms", "other"]


def test_password_rules():
    assert snmp_passwords.check("authentication", "Fabric-2026x") == "Fabric-2026x"
    for bad in ("Short1!", "has space 1A", "Quote'1Aa", "what?1Aa", "alllowercase", "x" * 65 + "A1"):
        with pytest.raises(snmp_passwords.PasswordError):
            snmp_passwords.check("authentication", bad)


def test_steps_answer_both_prompts_and_hide_the_password():
    steps = snmp_passwords.steps("nms", "sha2-256", "aes256", {"auth": "Auth-2026x", "priv": "Priv-2026x"})
    assert [s.command for s in steps] == ["system-view", "snmp-agent usm-user v3 nms authentication-mode sha2-256",
                                          "snmp-agent usm-user v3 nms privacy-mode aes256", "commit", "return", "save"]
    assert steps[1].questions == (("Enter Password:", "Auth-2026x"), ("Confirm Password:", "Auth-2026x"))
    assert steps[2].hide == ("Priv-2026x",)
    assert "Auth-2026x" not in snmp_passwords.plan_text({"nms": {"auth": "sha2-256", "priv": "aes256"}})


USERS = """<data><snmp xmlns="urn:huawei:yang:huawei-snmp"><usm-users><usm-user><user-name>nms</user-name>
<group-name>FABRIC-RO</group-name><auth-protocol>sha2-256</auth-protocol><priv-protocol>aes256</priv-protocol>
</usm-user></usm-users></snmp></data>"""


def test_users_are_read_from_netconf():
    assert snmp_passwords.users(USERS) == {"nms": ("FABRIC-RO", "sha2-256", "aes256")}


class Switch:
    """Asks for each password twice; keeps the algorithms of a user typed in full."""

    def __init__(self):
        self.users: dict[str, list[str]] = {}
        self.typed: list[str] = []

    def answer(self, step):
        words = step.command.split()
        if words[:3] == ["snmp-agent", "usm-user", "v3"]:
            assert [q for q, _ in step.questions] == ["Enter Password:", "Confirm Password:"] and step.hide
            self.typed.append(step.questions[0][1])
            self.users.setdefault(words[3], ["", ""])[words[4] == "privacy-mode"] = words[5]
        return step.expect or ""


class FakeAnnet:
    def __init__(self, diff=""):
        self.switches = {"leaf1": Switch(), "leaf2": Switch()}
        self.diffs = diff
        self.sent: list[tuple[str, str]] = []

    async def commands(self, device, ip, steps):
        for step in steps:
            self.sent.append((device, step.command))
            switch_cli.check_output(step, self.switches[device].answer(step))
        return ["" for _ in steps]

    async def diff(self, device):
        return self.diffs


class FakeReader:
    def __init__(self, annet, wrong=False):
        self.annet, self.wrong = annet, wrong

    async def get_state(self, device, subtree):
        users = self.annet.switches[device].users
        return "<data><snmp xmlns='urn:huawei:yang:huawei-snmp'><usm-users>" + "".join(
            f"<usm-user><user-name>{u}</user-name><group-name>FABRIC-RO</group-name>"
            f"<auth-protocol>{'md5' if self.wrong else a}</auth-protocol><priv-protocol>{p}</priv-protocol></usm-user>"
            for u, (a, p) in users.items()) + "</usm-users></snmp></data>"


@pytest.fixture
def make(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))

    def build(diff="", wrong=False):
        nb = two_active_leaves()
        run(snmp_access.set_reader(nb, "example", "nms"))
        annet = FakeAnnet(diff)
        verifier = Verifier(annet, FakePrometheus(), nb, VerifySettings(collector_timeout=0.3, poll_interval=0.01))
        jobs = snmp_passwords.PasswordJobs(DeployJobs(sessions, annet, nb, verifier), FakeReader(annet, wrong))
        return jobs, annet, nb

    return build


def test_passwords_are_write_only_and_masked(make):
    jobs, _, nb = make()
    readers = run(snmp_access.get(nb, "example"))["readers"]
    with pytest.raises(snmp_passwords.PasswordError, match="different"):
        run(snmp_passwords.set_passwords(jobs.sessions, "example", readers, "nms", "Same-2026x", "Same-2026x", "alice"))
    with pytest.raises(snmp_passwords.PasswordError, match="no reader"):
        run(snmp_passwords.set_passwords(jobs.sessions, "example", readers, "other", "Auth-2026x", "Priv-2026x", "alice"))
    run(snmp_passwords.set_passwords(jobs.sessions, "example", readers, "nms", "Auth-2026x", "Priv-2026x", "alice"))
    assert run(snmp_passwords.known(jobs.sessions, "example")) == ["nms"]
    assert redact.redact("typed Auth-2026x and Priv-2026x") == "typed ****** and ******"
    run(snmp_passwords.forget(jobs.sessions, "example", "nms", "alice"))
    assert run(snmp_passwords.known(jobs.sessions, "example")) == []


def requested(jobs, nb):
    readers = run(snmp_access.get(nb, "example"))["readers"]
    run(snmp_passwords.set_passwords(jobs.sessions, "example", readers, "nms", "Auth-2026x", "Priv-2026x", "alice"))

    async def scenario():
        job_id = await jobs.request("alice", "example", ["nms"])
        await jobs.approve(job_id, "bob")
        await jobs.wait()
        return await get_deploy(jobs.sessions, job_id, jobs.KIND)

    return run(scenario())


def test_the_job_types_the_passwords_switch_by_switch_and_verifies(make):
    jobs, annet, nb = make()
    job = requested(jobs, nb)
    assert job["status"] == "verified", job["log"]
    assert [d for d, c in annet.sent if c == "commit"] == ["leaf1", "leaf2"]
    for switch in annet.switches.values():
        assert switch.users == {"nms": ["sha2-256", "aes256"]} and switch.typed == ["Auth-2026x", "Priv-2026x"]
    assert "Auth-2026x" not in str(job)


def test_each_readers_state_is_read_from_the_switches(make):
    jobs, annet, nb = make()
    wanted = {"auth": "sha2-256", "priv": "aes256"}
    assert snmp_passwords.reader_state(wanted, None) == "missing"
    assert snmp_passwords.reader_state(wanted, ("FABRIC-RO", "noauth", "nopriv")) == "no-passwords"
    assert snmp_passwords.reader_state(wanted, ("FABRIC-RO", "sha2-256", "aes128")) == "different"
    assert snmp_passwords.reader_state(wanted, ("FABRIC-RO", "sha2-256", "aes256")) == "ok"
    assert requested(jobs, nb)["status"] == "verified"
    assert run(jobs.switch_state("example")) == [{"device": d, "readers": {"nms": "ok"}} for d in ("leaf1", "leaf2")]

    async def refuses(device, _filter):
        raise OSError("no route")
    jobs.reader.get_state = refuses
    assert run(jobs.switch_state("example"))[0] == {"device": "leaf1", "error": "could not read it: OSError"}


def test_the_job_refuses_an_out_of_sync_switch_and_a_wrong_algorithm(make):
    jobs, annet, nb = make(diff="+ snmp-agent usm-user v3 nms\n")
    job = requested(jobs, nb)
    assert job["status"] == "failed" and "out of sync" in job["error"] and not annet.sent
    jobs, annet, nb = make(wrong=True)
    job = requested(jobs, nb)
    assert job["status"] == "failed" and "md5" in job["error"]
    assert [s["status"] for s in job["steps"]] == ["failed", "skipped"]


def test_the_api_never_returns_a_password(database_url):
    from fastapi.testclient import TestClient

    from orchestrator.app import _intent_unfrozen, app, current_account, current_user, get_accounts, get_netbox, get_settings
    from orchestrator.auth import Accounts
    from orchestrator.platform_settings import PlatformSettings

    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    accounts, settings, nb = Accounts(sessions), PlatformSettings(sessions), example_netbox()
    run(accounts.create_user("olga", "olga-password", "operator"))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides.update({get_accounts: lambda: accounts, get_settings: lambda: settings, get_netbox: lambda: nb,
                                     _intent_unfrozen: lambda: None})
    try:
        client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        client.post("/auth/login", json={"username": "olga", "password": "olga-password"})
        assert client.put("/fabrics/example/snmp/readers/nms", json={}).status_code == 200
        assert client.put("/fabrics/example/snmp/readers/nms/passwords", json={"auth": "weak", "priv": "Priv-2026y"}).status_code == 400
        reply = client.put("/fabrics/example/snmp/readers/nms/passwords", json={"auth": "Auth-2026y", "priv": "Priv-2026y"})
        assert reply.status_code == 200 and "2026y" not in reply.text
        body = client.get("/fabrics/example/snmp")
        assert body.json()["passwords"] == ["nms"] and "2026y" not in body.text
        assert client.put("/fabrics/example/snmp/v2c", json={"on": False}).json()["v2c"] is False
        assert client.delete("/fabrics/example/snmp/readers/nms").status_code == 400   # the last reader without v2c
    finally:
        for dep in (get_accounts, get_settings, get_netbox, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
