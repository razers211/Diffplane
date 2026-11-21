"""Shared test setup: seed a fabric, then make the real switches active as the GUI does
(the GUI owns status and management addresses, the seed creates devices planned)."""

from orchestrator.lifecycle import Lifecycle
from seed.loader import Report, Seeder
import re
from pathlib import Path

import yaml

from seed.model import Fabric, parse

# Management addresses of the switches tests treat as real (leaf1 is; dc2-leaf1 is the
# second fabric's copy of it).
ACTIVE = {"leaf1": "172.24.0.11/24", "dc2-leaf1": "172.24.1.11/24"}


async def seed(nb, fabric: Fabric, active: dict[str, str] | None = None) -> Report:
    report = await Seeder(nb, fabric).run()
    names = {s.name for s in fabric.spines} | {leaf for p in fabric.mlag_pairs for leaf in p.leaves}
    for name, address in (ACTIVE if active is None else active).items():
        if name not in names:
            continue
        device = await nb.find_one("dcim/devices", name=name)
        status = device.get("status")
        if (status.get("value") if isinstance(status, dict) else status) != "active":
            await Lifecycle(nb).activate(name, address)
    return report


def pip_dc(copper: bool = False) -> Fabric:
    """example-pip as the example fabric's second data centre, with example's gateway MAC (a DC
    link needs the same one on both sides); `copper`: its leaves the copper CE6881H-48T6CQ-K."""
    data = yaml.safe_load((Path(__file__).resolve().parents[1] / "seed" / "fabrics" / "example-pip.yaml").read_text())
    data["fabric"]["gateway_mac"] = "0000-5e00-0101"
    if copper:
        for pair in data["leaf_pairs"]:
            pair["model"] = "CE6881H-48T6CQ-K"
    return parse(data)


def dc2_data() -> dict:
    """The small example fabric again as another DC `dc2`: same prefixes, VLANs and ASN, switches named dc2-<name>."""
    text = (Path(__file__).resolve().parent / "fixtures" / "example-small.yaml").read_text()
    data = yaml.safe_load(re.sub(r"\b(leaf|spine)(\d+)\b", r"dc2-\1\2", text))
    data["fabric"]["name"] = "dc2"
    return data
