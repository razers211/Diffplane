"""The Annet worker (orchestrator/annet_worker.py) with the real, pinned Annet: one test per command
the orchestrator calls, since Annet's internal API is not stable across versions.

No NetBox and no switch: Annet's own file storage holds the device, a small generator package
here makes its config, and `--config <dir>` stands in for the running config a switch would give.
"""

import asyncio
import textwrap

import pytest

from orchestrator.annet import AnnetError, AnnetRunner

GENERATORS = '''
from annet.generators import PartialGenerator


class Name(PartialGenerator):
    TAGS = ["system"]

    def acl_huawei(self, _):
        return """
        sysname
        """

    def run_huawei(self, device):
        if device.hostname == "broken":
            raise RuntimeError("generator exploded")
        yield "sysname", device.hostname


def get_generators(store):
    return [Name(store)]
'''


@pytest.fixture
def annet(tmp_path, monkeypatch):
    (tmp_path / "testgens").mkdir()
    (tmp_path / "testgens" / "__init__.py").write_text(GENERATORS)
    (tmp_path / "inventory.yaml").write_text(textwrap.dedent("""\
        devices:
          - fqdn: leaf1
            hw_model: Huawei CE6881H-48S6CQ
          - fqdn: broken
            hw_model: Huawei CE6881H-48S6CQ
        """))
    (tmp_path / "running").mkdir()
    (tmp_path / "running" / "leaf1.cfg").write_text("#\nsysname old-name\n#\n")
    (tmp_path / "context.yml").write_text(textwrap.dedent(f"""\
        fetcher: {{default: {{adapter: gnetcli}}}}
        deployer: {{default: {{adapter: gnetcli}}}}
        generators: {{default: [testgens]}}
        storage: {{default: {{adapter: file, params: {{path: {tmp_path / "inventory.yaml"}}}}}}}
        rulebook: {{default: {{module: rulebook.texts}}}}
        context: {{default: {{fetcher: default, deployer: default, generators: default, storage: default, rulebook: default}}}}
        selected_context: default
        """))
    monkeypatch.setenv("ANN_CONTEXT_CONFIG_PATH", str(tmp_path / "context.yml"))
    for name in ("ANN_SELECTED_CONTEXT", "ANN_GENERATORS_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    return AnnetRunner(cwd=tmp_path, timeout=60)


def scenario(annet: AnnetRunner, steps):
    async def go():
        try:
            return await steps()
        finally:
            await annet.close()
    return asyncio.run(go())


def test_gen_diff_patch_and_show_current_through_one_worker(annet, tmp_path):
    running = str(tmp_path / "running")

    async def steps():
        return (
            await annet.gen("leaf1"),
            await annet._labelled("diff", "leaf1", ".cfg", "--no-progress", "--config", running),
            await annet._labelled("patch", "leaf1", ".patch", "--no-progress", "--config", running),
            await annet._run("show", "current", "--config", running, "leaf1"),
        )

    generated, diff, patch, current = scenario(annet, steps)
    assert generated == "sysname leaf1\n"
    assert "- sysname old-name" in diff and "+ sysname leaf1" in diff
    assert patch.strip() == "sysname leaf1"
    assert current["items"] == [["leaf1.cfg", "#\nsysname old-name\n#\n", False]]


def test_failures_are_errors_never_empty_results(annet):
    async def steps():
        with pytest.raises(AnnetError, match="(?s)annet gen failed: .*generator exploded"):
            await annet.gen("broken")
        with pytest.raises(AnnetError, match="was not found in NetBox"):   # Annet only logs this one
            await annet.gen("leaf9")
        return await annet.gen("leaf1")   # the worker survived both

    assert scenario(annet, steps) == "sysname leaf1\n"


def test_a_failed_connection_is_tried_again_other_errors_are_not(tmp_path, monkeypatch):
    import orchestrator.annet as annet_module

    monkeypatch.setattr(annet_module, "DIAL_RETRY_SECONDS", 0)
    dial = {"code": 0, "items": [], "errors": [
        "ERROR config error GnetcliException('AioRpcError failed to dial any of given endpoints: [10.0.0.1:22]')"]}
    ok = {"code": 0, "items": [["leaf1.cfg", "", False]], "errors": []}
    other = {"code": 0, "items": [], "errors": ["ERROR leaf1 - Error: Unrecognized command"]}
    runner = AnnetRunner(cwd=tmp_path, timeout=60)

    def calls(replies):
        sent = []

        async def call(argv, timeout):
            sent.append(argv[0])
            return replies[len(sent) - 1]
        runner._call = call
        return sent

    sent = calls([dial, ok])
    assert asyncio.run(runner._run("diff", "leaf1")) == ok and sent == ["diff", "diff"]
    sent = calls([dial, dial, dial, ok])
    assert asyncio.run(runner._run("deploy", "leaf1")) == dial and len(sent) == 3   # two retries at most
    sent = calls([other, ok])
    assert asyncio.run(runner._run("deploy", "leaf1")) == other and sent == ["deploy"]   # never a rejected patch
    sent = calls([dial, ok])
    assert asyncio.run(runner._run("gen", "leaf1")) == dial and sent == ["gen"]   # gen never connects
