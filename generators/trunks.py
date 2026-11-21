from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import all_trunks, device_value, fabric_links, retired_trunks, single_trunks, trunk_description


def _retired(device: Any):
    """Deleted trunks still on the switch, without what NetBox now uses again: a trunk of the
    same name, or a member that joined another trunk, belongs to its current owner."""
    current = all_trunks(device)
    names = {t.name for t in current}
    members = {m for t in current for m in t.members}
    for trunk in retired_trunks(device):
        if trunk.name not in names:
            yield trunk.name, [m for m in trunk.members if m not in members]


class Trunks(PartialGenerator):
    """Eth-Trunks of one device alone (no M-LAG), on a leaf or a spine:
    LACP static, members joined with `eth-trunk <n>`. M-LAG trunks and the peer-link are Mlag's.

    Owns only the trunks NetBox knows, plus deleted ones (any kind, retired with their members)
    until a verified deploy removed them; hand-made trunks are left alone. `undo interface
    Eth-Trunk<n>` comes after the members left it (rulebook/texts/huawei.order).
    """

    TAGS = ["trunks"]

    def acl_huawei(self, device: Any) -> str:
        lines = []
        owned = [(t.name, t.members) for t in single_trunks(device)] + list(_retired(device))
        links = {link.interface for link in fabric_links(device)}   # a fabric link's port settings are FabricLinks'
        for name, members in owned:
            lines += [
                # Owned entirely, so it may be deleted.
                f"interface {name}  %cant_delete=0",
                *(["    description ~"] if name not in links else []),
                "    mode ~",
                "    dfs-group ~",
            ]
            for member in members:
                lines += [f"interface {member}", "    eth-trunk ~"]
        return "\n".join(lines)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        manual = device_value(device, "manual_trunks", [])   # no `mode` on the switch (imported fabrics)
        links = {link.interface for link in fabric_links(device)}   # a fabric LAG's description is FabricLinks'
        for trunk in single_trunks(device):
            with self.block("interface", trunk.name):
                if trunk.name not in links and (text := trunk_description(device, trunk.name)) is not None:
                    yield "description", text
                if trunk.name not in manual:
                    yield "mode lacp-static"
            for member in trunk.members:
                with self.block("interface", member):
                    yield "eth-trunk", trunk.number
