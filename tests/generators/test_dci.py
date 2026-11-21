"""DC links: the border's DCI lines are the platform's while a link has them and, retired,
until a verified deploy removed them; a hand-made peer is never touched."""

from functools import cache

from tests.generators.harness import patch_to, render
from tests.generators.test_golden import dci_devices


@cache
def linked():
    return {d.hostname: d for d in dci_devices("example")}


def test_the_generated_border_needs_no_patch():
    leaf = linked()["leaf1"]
    assert patch_to(render(leaf), leaf) == ""


def unlinked():
    """The same example after the DC link was deleted through the API (its peers and port retired)."""
    import asyncio

    from orchestrator import dci
    from seed.model import load
    from tests.generators.harness import devices_from_netbox
    from tests.generators.test_golden import ROOT, example_dci
    from tests.helpers import seed
    from tests.seed.fake_netbox import FakeNetBox

    nb = FakeNetBox()
    asyncio.run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    asyncio.run(example_dci(nb))
    asyncio.run(dci.delete_link(nb, "example-pip"))
    return {d.hostname: d for d in devices_from_netbox(nb, "example")}


def test_deleting_the_link_removes_everything_it_made_and_nothing_else():
    after = unlinked()["leaf1"]
    assert after.config_context["dci"]["retired"]["leaf1"] == {
        "peers": ["10.255.10.2", "10.40.0.3", "10.40.0.4"], "ports": ["100GE1/0/3"]}
    running = render(linked()["leaf1"]).replace("bgp 65001\n", "bgp 65001\n  peer 192.0.2.1 as-number 65100\n", 1)
    lines = [line.strip() for line in patch_to(running, after).splitlines()]
    # `undo vxlan vni <n>` (Annet's `vxlan vni *` rule) for the mapped VNI:.
    for removal in ("undo peer 10.40.0.3", "undo peer 10.40.0.4", "undo peer 10.255.10.2",
                    "undo vxlan vni 60000", "undo vni 60000 head-end peer-list protocol bgp",
                    "undo peer 10.10.0.1 import reoriginate", "undo ip address 10.255.10.1 255.255.255.252"):
        assert removal in lines, "\n".join(lines)
    assert not any("192.0.2.1" in line for line in lines)
