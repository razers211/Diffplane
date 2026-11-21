"""The draft preview: the patch a switch would get with a change set applied, from the
change set's copy of NetBox, printed as `annet patch` prints it."""

import asyncio

import pytest

from orchestrator.annet_view import devices_from_netbox, render
from orchestrator.draft_preview import preview
from orchestrator.netbox_clone import clone
from orchestrator.segments import Segments
from tests.orchestrator.test_deploys import two_active_leaves


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "test-community")


def test_a_segment_staged_on_the_copy_is_the_whole_patch(community):
    live = two_active_leaves()
    running = {d.name: render(d) for d in devices_from_netbox(live, "example") if d.name == "leaf1"}

    async def staged():
        copy = await clone(live)
        segments = Segments(copy)
        await segments.create("example", "web", 10500, 500)
        await segments.attach("example", "leaf1", "10GE1/0/3", 10500, None)
        return copy

    copy = asyncio.run(staged())
    request = {"site": "example", "objects": {e: {str(i): o for i, o in objs.items()} for e, objs in copy.objects.items()},
               "configs": running}
    patches = preview(request)["patches"]
    unchanged = preview({**request, "objects": asyncio.run(clone(live)).objects})["patches"]

    assert unchanged == {"leaf1": ""}   # the copy alone changes nothing
    patch = patches["leaf1"]
    assert patch.startswith("bridge-domain 500\n  vxlan vni 10500\n") and "interface 10GE1/0/3.1 mode l2\n" in patch
    assert "\n  quit\n" in patch and patch.endswith("\n")   # `annet patch`'s form
