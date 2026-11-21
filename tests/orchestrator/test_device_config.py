import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from orchestrator.annet import PARALLEL, AnnetError, AnnetRunner
from orchestrator.app import app, get_annet, get_netbox
from orchestrator.netbox import NetBoxError
from tests.seed.fake_netbox import FakeNetBox


class FakeAnnet:
    def __init__(self, result: str | Exception = "sysname leaf1\n"):
        self.result = result
        self.calls: list[str] = []

    async def gen(self, device: str) -> str:
        self.calls.append(device)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def netbox() -> FakeNetBox:
    nb = FakeNetBox()
    asyncio.run(nb.create("dcim/devices", {"name": "leaf1", "site": 1}))
    return nb


def client(netbox, annet) -> TestClient:
    app.dependency_overrides[get_netbox] = lambda: netbox
    app.dependency_overrides[get_annet] = lambda: annet
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_returns_generated_config(netbox):
    annet = FakeAnnet()

    response = client(netbox, annet).get("/devices/leaf1/config")

    assert response.status_code == 200
    assert response.json() == {"device": "leaf1", "config": "sysname leaf1\n"}
    assert annet.calls == ["leaf1"]


def test_unknown_device_is_404_without_running_annet(netbox):
    annet = FakeAnnet()

    response = client(netbox, annet).get("/devices/leaf9/config")

    assert response.status_code == 404
    assert annet.calls == []


def test_annet_failure_is_500(netbox):
    response = client(netbox, FakeAnnet(AnnetError("annet gen failed: boom"))).get("/devices/leaf1/config")

    assert response.status_code == 500
    assert "boom" in response.json()["detail"]


def test_netbox_down_is_503(netbox):
    class Down(FakeNetBox):
        async def find(self, endpoint, **filters):
            raise NetBoxError("cannot reach NetBox")

    response = client(Down(), FakeAnnet()).get("/devices/leaf1/config")

    assert response.status_code == 503


# -- AnnetRunner against a fake worker (orchestrator/annet_worker.py's protocol) ---------------

FAKE_WORKER = """
import json, os, sys, time
runs = 0
for line in sys.stdin:
    request = json.loads(line)
    args, env = request["argv"], request["env"]
    device, command = args[-1], args[0]
    suffix = ".patch" if command == "patch" else ".cfg"
    runs += 1
    reply = {"code": 0, "items": [], "errors": []}
    if "FAKE_ARGS" in env:
        open(env["FAKE_ARGS"], "w").write(" ".join(args))
    if device == "broken":
        reply = {"code": 1, "items": [], "errors": ["ERROR Traceback: generator exploded"]}
    elif device == "slow":
        time.sleep(5)
    elif device == "crash":
        sys.exit(3)
    elif device == "missing":
        reply["errors"] = ["ERROR No devices found for NetboxQuery"]
    elif device == "genfail":   # a failed device comes back labelled with its bare name
        reply["items"] = [["genfail", "Traceback (most recent call last):\\n"
                           "generators.fabric.UnknownSegment: VNI 999 is not a known segment\\n", True]]
    elif device == "rejected":   # gnetcli writes a transcript, annet logs only the device's error
        out = os.path.join(env["FAKE_LOGS"], "2026-10-06-23-00", f"rejected_{time.time():.0f}")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "w").write("t: cmd: ip vpn-instance FW\\nt: cmd: ip route-static vpn-instance FW 10.100.0.0 "
                             "255.255.255.0 vpn-instance web-500\\nt: Error: The configure is invalid, because "
                             "the vrf-session not exist.\\n")
        reply = {"code": 2, "items": [], "errors": ["ERROR rejected - Error: The configure is invalid"]}
    elif device == "logged":
        reply["errors"] = ["ERROR something went wrong"]
    elif device == "quiet":
        pass
    elif device == "other":
        reply["items"] = [["leaf9.cfg", "sysname leaf9\\n", False]]
    else:
        body = "args=" + " ".join(args) + "\\nexact=" + env.get("NETBOX_EXACT_HOST_FILTER", "") + \\
            f"\\npid={os.getpid()} run={runs}\\n"
        reply["items"] = [[device + suffix, body, False]]
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
"""


def runner(tmp_path, timeout=10.0) -> AnnetRunner:
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER)
    return AnnetRunner(command=[sys.executable, str(script)], cwd=tmp_path, timeout=timeout)


def once(annet: AnnetRunner, call: str, *args):
    """One call on its own event loop, the runner's workers stopped afterwards."""
    async def go():
        try:
            return await getattr(annet, call)(*args)
        finally:
            await annet.close()
    return asyncio.run(go())


def first(output: str) -> str:
    return output.splitlines()[0]


def test_runner_calls_gen_for_one_device_with_exact_filter(tmp_path):
    output = once(runner(tmp_path), "gen", "leaf1")

    assert output.splitlines()[:2] == ["args=gen --no-color leaf1", "exact=true"]


def test_runner_reports_stderr_on_failure(tmp_path):
    with pytest.raises(AnnetError, match="generator exploded"):
        once(runner(tmp_path), "gen", "broken")


def test_runner_times_out(tmp_path):
    with pytest.raises(AnnetError, match="timed out"):
        once(runner(tmp_path, timeout=0.5), "gen", "slow")


@pytest.mark.parametrize("name", ["-h", "--dest=/tmp", "leaf1 leaf2", "", "../x", "a;b"])
def test_runner_rejects_unsafe_names(tmp_path, name):
    with pytest.raises(AnnetError, match="invalid device name"):
        once(runner(tmp_path), "gen", name)


def test_secrets_are_masked_in_returned_config(netbox, monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")
    annet = FakeAnnet("snmp-agent community read ANNET-TEST alias ro-0a1b2c3d\nsysname leaf1\n")

    body = client(netbox, annet).get("/devices/leaf1/config").json()

    assert "ANNET-TEST" not in body["config"]
    assert "snmp-agent community read ****** alias ro-0a1b2c3d" in body["config"]


def test_runner_diff_arguments_and_empty_result(tmp_path):
    assert first(once(runner(tmp_path), "diff", "leaf1")) == "args=diff --no-color --no-progress leaf1"


def test_runner_empty_output_means_no_changes(tmp_path):
    assert once(runner(tmp_path), "diff", "quiet") == ""
    assert once(runner(tmp_path), "patch", "quiet") == ""


@pytest.mark.parametrize("device, message", [
    ("genfail", "failed for genfail"),       # exit 0, failure printed as its own section
    ("missing", "not found in NetBox"),      # exit 0, only an ERROR log line
    ("logged", "reported errors"),
    ("other", "failed for leaf9.cfg"),       # a result for another device
])
def test_runner_never_reads_a_failure_as_no_changes(tmp_path, device, message):
    for call in ("gen", "diff", "patch"):
        with pytest.raises(AnnetError, match=message):
            once(runner(tmp_path), call, device)


def test_runner_patch_and_deploy_arguments(tmp_path, monkeypatch):
    r = runner(tmp_path)
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args"))

    assert first(once(r, "patch", "leaf1", ["Snmp", "Ntp"])) == "args=patch --no-color --no-progress -g Snmp,Ntp leaf1"
    assert once(r, "deploy", "leaf1") is None
    assert (tmp_path / "args").read_text() == "deploy --no-ask-deploy --no-progress --no-label --no-color leaf1"


def test_runner_rejects_bad_generator_names(tmp_path):
    with pytest.raises(AnnetError, match="invalid generator"):
        once(runner(tmp_path), "patch", "leaf1", ["Snmp; rm"])


def test_failed_deploy_shows_the_end_of_the_transcript(tmp_path, monkeypatch):
    logs = tmp_path / "deploy-logs"
    monkeypatch.setenv("FAKE_LOGS", str(logs))
    run = runner(tmp_path)
    run.logs_dir = logs

    with pytest.raises(AnnetError) as excinfo:
        once(run, "deploy", "rejected")

    message = str(excinfo.value)
    assert "cmd: ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500" in message
    assert message.index("Error: The configure is invalid") < message.index("last lines of the deploy transcript")


def test_failed_deploy_without_transcript_keeps_the_error(tmp_path):
    run = runner(tmp_path)
    run.logs_dir = tmp_path / "none"

    with pytest.raises(AnnetError, match="something went wrong") as excinfo:
        once(run, "deploy", "logged")
    assert "transcript" not in str(excinfo.value)



def test_switch_commands_overlap_up_to_the_bound_each_on_its_own_gnetcli_port(tmp_path):
    """gnetcli_server's fixed port broke a second run at once: each run now asks for a
    free port (deploy/annet/context.yml, tests/generators/test_context.py), and at most PARALLEL
    connect at the same time."""
    script = tmp_path / "slow_worker.py"
    script.write_text(
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    device = json.loads(line)['argv'][-1]\n"
        "    start = time.time(); time.sleep(0.4)\n"
        "    print(json.dumps({'code': 0, 'errors': [], 'items': [[device + '.cfg', f'{start} {time.time()}', False]]}), flush=True)\n")
    annet = AnnetRunner(command=[sys.executable, str(script)], cwd=tmp_path)
    devices = [f"leaf{i}" for i in range(PARALLEL + 1)]

    async def every():
        try:
            return await asyncio.gather(*(annet.diff(d) for d in devices))
        finally:
            await annet.close()

    runs = [tuple(map(float, out.split())) for out in asyncio.run(every())]
    most = max(sum(s <= t < e for s, e in runs) for t, _ in runs)
    assert most == PARALLEL                                  # side by side, never more than the bound


def pids(outputs: list[str]) -> list[str]:
    return [next(w for w in out.split() if w.startswith("pid=")) for out in outputs]


def test_a_worker_serves_one_run_after_another(tmp_path):
    annet = runner(tmp_path)

    async def scenario():
        try:
            return [await annet.gen("leaf1"), await annet.diff("leaf1"), await annet.gen("leaf2")]
        finally:
            await annet.close()

    outputs = asyncio.run(scenario())
    assert len(set(pids(outputs))) == 1                      # Annet loaded once
    assert [o.split()[-1] for o in outputs] == ["run=1", "run=2", "run=3"]


def test_a_stuck_or_dead_worker_is_replaced(tmp_path):
    annet = runner(tmp_path, timeout=0.5)

    async def scenario():
        try:
            before = await annet.gen("leaf1")
            with pytest.raises(AnnetError, match="timed out"):
                await annet.gen("slow")
            with pytest.raises(AnnetError, match="worker stopped"):
                await annet.gen("crash")
            return before, await annet.gen("leaf1")
        finally:
            await annet.close()

    before, after = asyncio.run(scenario())
    assert pids([before]) != pids([after]) and after.split()[-1] == "run=1"


def test_a_generator_change_starts_a_new_worker(tmp_path):
    (tmp_path / "generators").mkdir()
    module = tmp_path / "generators" / "snmp.py"
    module.write_text("A = 1\n")
    annet = runner(tmp_path)

    async def scenario():
        try:
            first_run = await annet.gen("leaf1")
            module.write_text("A = 2  # changed\n")
            import os
            os.utime(module, ns=(module.stat().st_mtime_ns + 10**9,) * 2)
            return first_run, await annet.gen("leaf1")
        finally:
            await annet.close()

    before, after = asyncio.run(scenario())
    assert pids([before]) != pids([after])


def test_each_run_takes_the_platforms_current_settings(tmp_path, monkeypatch):
    script = tmp_path / "env_worker.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    r = json.loads(line)\n"
        "    print(json.dumps({'code': 0, 'errors': [], 'items': [[r['argv'][-1] + '.cfg', r['env'].get('SNMP_LOCATION', ''), False]]}), flush=True)\n")
    annet = AnnetRunner(command=[sys.executable, str(script)], cwd=tmp_path)

    async def scenario():
        try:
            monkeypatch.setenv("SNMP_LOCATION", "rack-1")
            one = await annet.gen("leaf1")
            monkeypatch.setenv("SNMP_LOCATION", "rack-2")
            return one, await annet.gen("leaf1")
        finally:
            await annet.close()

    assert asyncio.run(scenario()) == ("rack-1\n", "rack-2\n")
