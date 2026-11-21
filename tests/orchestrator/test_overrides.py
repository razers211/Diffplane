"""The overrides catalogue: every key a generator reads is listed, nothing else is written."""

import asyncio
import copy
import re
from pathlib import Path

import pytest

from orchestrator import overrides
from orchestrator.segments import SegmentError

ROOT = Path(__file__).parents[2] / "generators"
READS = {"fabric_value": "fabric", "device_value": "switch"}


def read_keys() -> set[tuple[str, str]]:
    found = set()
    for path in ROOT.glob("*.py"):
        source = path.read_text()
        for call, scope in READS.items():
            found |= {(scope, key) for key in re.findall(rf'{call}\(device, "(\w+)"', source)}
    return found


def test_every_key_the_generators_read_is_in_the_catalogue():
    assert len(read_keys()) > 20
    assert read_keys() - set(overrides.KEYS) == set()


def test_the_catalogue_says_what_each_one_means():
    assert len(overrides.KEYS) == len(overrides.CATALOGUE)
    assert all(o.text and o.derived and o.generator and o.area for o in overrides.CATALOGUE)


def test_unknown_keys_are_refused():
    overrides.check({"fabric": {"link_mtu": None}, "devices": {"a": {"heartbeat": {}}},
                     "segments": {"10": {"rd": "1:1"}}, "services": {"FW": {"bfd": False}}})
    with pytest.raises(SegmentError, match="unknown overrides: devices.link_mtu, fabric.nope, segments.mac, surprise"):
        overrides.check({"fabric": {"nope": 1}, "devices": {"a": {"link_mtu": 1}},
                         "segments": {"10": {"mac": "x"}}, "surprise": {}})


DOCUMENT = {"fabric": {"link_mtu": None}, "devices": {"rack1": {"evpn_peers": ["10.0.0.1"], "kept_vnis": [10777]}},
            "segments": {"10500": {"l2_rt": "65001:500"}}}


def imported(document=None):
    from tests.seed.fake_netbox import FakeNetBox

    netbox = FakeNetBox()
    asyncio.run(netbox.create("extras/config-contexts", {"name": "overrides-dc", "data": {
        "overrides": document or copy.deepcopy(DOCUMENT), "misfits": {"rack1": ["M-LAG 5 has no member ports"]}}}))
    return netbox


def stored(netbox):
    return asyncio.run(netbox.find_one("extras/config-contexts", name="overrides-dc"))["data"]


def test_every_value_is_listed_in_plain_words_with_what_was_left_unmanaged():
    shown = asyncio.run(overrides.view(imported(), "dc"))
    assert shown["imported"] and shown["misfits"] == {"rack1": ["M-LAG 5 has no member ports"]}
    assert {e["path"] for e in shown["entries"]} == {"link_mtu", "devices/rack1/evpn_peers",
                                                     "devices/rack1/kept_vnis", "segments/10500/l2_rt"}
    assert all(e["text"] and e["area"] and e["derived"] for e in shown["entries"])


def test_make_it_standard_removes_one_value_and_its_empty_group():
    netbox = imported()
    asyncio.run(overrides.remove(netbox, "dc", "segments/10500/l2_rt"))
    asyncio.run(overrides.remove(netbox, "dc", "link_mtu"))
    data = stored(netbox)
    assert data["overrides"] == {"fabric": {}, "devices": DOCUMENT["devices"], "segments": {}}
    assert data["misfits"]


@pytest.mark.parametrize("path, reason", [("devices/rack1/kept_vnis", "cannot be made standard"),
                                          ("devices/rack2/evpn_peers", "does not keep"),
                                          ("devices/rack1/nonsense", "unknown override"),
                                          ("devices/evpn_peers", "unknown override")])
def test_what_cannot_be_made_standard_is_refused(path, reason):
    with pytest.raises(SegmentError, match=reason):
        asyncio.run(overrides.remove(imported(), "dc", path))


def test_a_fabric_the_platform_built_has_no_overrides():
    from tests.seed.fake_netbox import FakeNetBox

    assert asyncio.run(overrides.view(FakeNetBox(), "dc")) == {"imported": False, "entries": [], "misfits": {}}
    with pytest.raises(SegmentError, match="keeps nothing"):
        asyncio.run(overrides.put(FakeNetBox(), "dc", "link_mtu", 1500))


def test_only_an_import_creates_the_document():
    root = Path(__file__).parents[2]
    allowed = {"orchestrator/import_build.py"}
    for source in [*root.glob("orchestrator/*.py"), *root.glob("seed/*.py")]:
        if str(source.relative_to(root)) not in allowed:
            for call in re.findall(r"create\(\s*\"extras/config-contexts\".{0,200}", source.read_text(), re.S):
                assert "overrides" not in call, source
