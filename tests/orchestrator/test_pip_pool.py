"""An existing fabric moved to the PIP layout (owner, 2026-11-19) generates what a fabric seeded with
it does: the example-pip goldens, from the same YAML seeded without its PIP pool, then converted."""

import asyncio
import re
from pathlib import Path

import pytest
import yaml

from orchestrator.pip_pool import set_pool
from orchestrator.segments import SegmentError
from seed.model import parse
from tests.generators.harness import devices_from_netbox, render
from tests.helpers import seed
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]
# Which free PIP each switch gets depends on the order they are allocated in (the seed's pairs, ours names).
PIP = re.compile(r"10\.40\.1\.\d+")


def without_pip() -> FakeNetBox:
    data = yaml.safe_load((ROOT / "seed" / "fabrics" / "example-pip.yaml").read_text())
    pool = data["fabric"]["prefixes"].pop("pip")
    assert pool == "10.40.1.0/24"
    nb = FakeNetBox()
    asyncio.run(seed(nb, parse(data)))
    return nb


def test_converting_gives_the_pip_goldens():
    nb = without_pip()
    before = {d.hostname: render(d) for d in devices_from_netbox(nb, "example-pip")}
    assert all("LoopBack2" not in c for c in before.values())   # the default layout first

    result = asyncio.run(set_pool(nb, "example-pip", "10.40.1.0/24"))

    assert result["switches"]
    for device in devices_from_netbox(nb, "example-pip"):
        golden = ROOT / "tests" / "golden" / "example-pip" / f"{device.hostname}.cfg"
        assert PIP.sub("PIP", render(device)) == PIP.sub("PIP", golden.read_text()), device.hostname
    assert asyncio.run(set_pool(nb, "example-pip", "10.40.1.0/24")) == {"prefix": "10.40.1.0/24", "switches": []}


def test_refusals():
    nb = without_pip()
    with pytest.raises(SegmentError, match="overlaps"):
        asyncio.run(set_pool(nb, "example-pip", "10.40.0.0/16"))
    asyncio.run(set_pool(nb, "example-pip", "10.40.1.0/24"))
    with pytest.raises(SegmentError, match="already has the PIP layout"):
        asyncio.run(set_pool(nb, "example-pip", "10.40.9.0/24"))


def test_the_conversion_patch_frees_loopback1_first_and_drops_the_old_route(monkeypatch):
    """The patch from a pair's config before to after: V300 refuses the VTEP address on LoopBack2
    while LoopBack1 still holds it, and the route to the peer's LoopBack0 (which carried the
    heartbeat over the peer-link) must go."""
    from annet.annlib.patching import match_row_to_acl
    from annet.annlib.rbparser.acl import compile_acl_text

    from generators.mlag_bypass import MlagBypass
    from tests.rulebook.patch import patch, use_our_rulebook

    use_our_rulebook(monkeypatch)
    nb = without_pip()
    before = {d.hostname: render(d) for d in devices_from_netbox(nb, "example-pip")}
    asyncio.run(set_pool(nb, "example-pip", "10.40.1.0/24"))
    for device in devices_from_netbox(nb, "example-pip"):
        if "interface LoopBack2" not in render(device):
            continue
        lines = patch(before[device.hostname], render(device)).splitlines()
        assert lines.index("interface LoopBack1") < lines.index("interface LoopBack2"), device.hostname
        assert lines.index("interface LoopBack2") > lines.index("ospf 1") if "ospf 1" in lines else True
        old = next(line for line in before[device.hostname].splitlines()
                   if line.startswith("ip route-static") and "preference 1" in line)
        assert f"undo {old.rsplit(' preference', 1)[0]}" in lines, device.hostname
        rules = compile_acl_text(MlagBypass(device.storage).acl_huawei(device), "huawei")
        assert match_row_to_acl(old, rules)[0] is not None, device.hostname
