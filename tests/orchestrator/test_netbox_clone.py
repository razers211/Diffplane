"""A NetBox copy for drafts: read through the client, it gives back the same objects."""

import asyncio

from orchestrator.netbox_clone import ENDPOINTS, clone
from tests.orchestrator.test_deploys import two_active_leaves


def test_a_copy_reads_back_like_the_original_and_changes_stay_in_it():
    original = two_active_leaves()

    async def scenario():
        copy = await clone(original)
        same = {e: await copy.find(e) == await original.find(e) for e in ENDPOINTS}
        device = await copy.find_one("dcim/devices", name="leaf1")
        await copy.update("dcim/devices", device["id"], {"serial": "draft"})
        made = await copy.create("extras/tags", {"name": "draft", "slug": "draft"})
        return same, made, await original.find_one("dcim/devices", name="leaf1")

    same, made, untouched = asyncio.run(scenario())

    assert all(same.values()), [e for e, ok in same.items() if not ok]
    assert untouched.get("serial") != "draft"
    assert made["id"] > max(i for store in original.objects.values() for i in store)
