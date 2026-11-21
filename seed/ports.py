"""Front-panel ports per switch model, so every port exists in NetBox to attach segments to.

The CE6800, CE8800 and CE9800 run one software with one syntax; only port names differ. So a
model's ports are learned from the first switch of it that the platform reads
(orchestrator/switch_inventory.py) and kept as its NetBox device type's interface templates;
`refresh` loads them here. Until then the models below are known from the start. A device of a
model known neither way gets only the ports its fabric YAML mentions, and can be planned only once
a switch of the model has been read.
"""

from seed.model import interface_type

BOOTSTRAP: dict[str, list[str]] = {
    # read over NETCONF
    "CE6881H-48S6CQ": [f"10GE1/0/{n}" for n in range(1, 49)] + [f"100GE1/0/{n}" for n in range(1, 7)],
    # Assumed: the 48S6CQ with 48 RJ45 10GE ports instead of SFP+.
    "CE6881H-48T6CQ-K": [f"10GE1/0/{n}" for n in range(1, 49)] + [f"100GE1/0/{n}" for n in range(1, 7)],
    # these names in running configs, read over SSH.
    "CE6881H-48T6CQ": [f"10GE1/0/{n}" for n in range(1, 49)] + [f"100GE1/0/{n}" for n in range(1, 7)],
}
# Bootstrap models whose port names were given, not read from a switch: blocked until one is read.
ASSUMED = {"CE6881H-48T6CQ-K"}
# RJ45 ports of the bootstrap models (`hard-type` Eth10GEPort).
_BOOTSTRAP_COPPER = {model: {f"10GE1/0/{n}" for n in range(1, 49)} for model in ("CE6881H-48T6CQ", "CE6881H-48T6CQ-K")}

# model -> {port: NetBox interface type}, read from NetBox's interface templates (refresh)
LEARNED: dict[str, dict[str, str]] = {}
# NetBox types of RJ45 ports, the only ones with `speed` and `negotiation`
COPPER_TYPES = {"100base-tx", "1000base-t", "2.5gbase-t", "5gbase-t", "10gbase-t"}


def _port_key(name: str) -> tuple:
    head, _, numbers = name.partition("GE")
    return (len(head), head, *(int(n) for n in numbers.split("/") if n.isdigit()))


def layout(model: str | None) -> dict[str, str]:
    """{port: NetBox type} in front-panel order; empty for a model not known yet."""
    if model in LEARNED:
        return LEARNED[model]
    copper = _BOOTSTRAP_COPPER.get(model or "", set())
    return {p: "10gbase-t" if p in copper else interface_type(p) for p in BOOTSTRAP.get(model or "", [])}


def ports_of(model: str | None) -> list[str]:
    return list(layout(model))


def known_models() -> list[str]:
    return sorted(set(BOOTSTRAP) | set(LEARNED))


def is_copper(interface_type: str | None) -> bool:
    return interface_type in COPPER_TYPES


async def refresh(nb) -> None:
    """Load every learned layout (device types with interface templates) from NetBox."""
    types = {t["id"]: t["model"] for t in await nb.find("dcim/device-types")}
    learned: dict[str, dict[str, str]] = {}
    for t in await nb.find("dcim/interface-templates"):
        model = types.get(((t.get("device_type") or {}).get("id")))
        if model:
            learned.setdefault(model, {})[t["name"]] = (t.get("type") or {}).get("value") or "other"
    LEARNED.clear()
    LEARNED.update({m: dict(sorted(p.items(), key=lambda kv: _port_key(kv[0]))) for m, p in learned.items()})
