"""Run our generators through Annet's own pipeline, the way `annet gen` does.

The pipeline itself is orchestrator/annet_view.py (the import preview uses it too); this adds
seeding a fabric YAML into the in-memory NetBox.
"""

from orchestrator.annet_view import (  # noqa: F401
    _STORAGE, StubDevice, StubStorage, check_acl_exclusive, devices_from_netbox, normalize, patch_to, render,
)
from tests.helpers import seed


def devices_from_fabric(path, prepare=None) -> list[StubDevice]:
    """Seed the fake NetBox from a fabric YAML and present its devices the way Annet's
    NetBox adapter does (name, role, interfaces with addresses and cable peers)."""
    import asyncio

    from seed.model import load
    from tests.seed.fake_netbox import FakeNetBox

    fabric = load(path)
    nb = FakeNetBox()
    asyncio.run(seed(nb, fabric))
    if prepare is not None:  # e.g. create segments and attachments through the orchestrator
        asyncio.run(prepare(nb))
    return devices_from_netbox(nb, fabric.slug)
