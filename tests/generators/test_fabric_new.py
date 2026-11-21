"""A fabric made in the GUI generates exactly what the same fabric seeded from YAML does."""

import asyncio
from pathlib import Path

import pytest
import yaml

from orchestrator.fabric_new import create
from seed.model import parse
from tests.generators.harness import devices_from_netbox, render
from tests.helpers import seed
from tests.orchestrator.test_fabric_new import wizard
from tests.seed.fake_netbox import FakeNetBox

FABRICS = Path(__file__).resolve().parents[2] / "seed" / "fabrics"


@pytest.mark.parametrize("name", ["example.yaml", "example-central.yaml", "example-central-spines.yaml"])
def test_gui_fabric_generates_like_its_yaml(name):
    data = yaml.safe_load((FABRICS / name).read_text())
    for pair in data["leaf_pairs"] + ([data["spine_pair"]] if data.get("spine_pair") else []):
        pair.pop("mlag_trunks", None)   # server trunks are made in the GUI afterwards
    from_yaml = FakeNetBox()
    asyncio.run(seed(from_yaml, parse(data)))
    from_gui = FakeNetBox()
    settings, batch = wizard(FABRICS / name)
    asyncio.run(create(from_gui, settings, batch))
    slug = parse(data).slug
    expected = {d.hostname: render(d) for d in devices_from_netbox(from_yaml, slug)}
    got = {d.hostname: render(d) for d in devices_from_netbox(from_gui, slug)}
    assert sorted(got) == sorted(expected)
    for host in expected:
        assert got[host] == expected[host], host
