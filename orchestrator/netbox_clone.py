"""A copy of NetBox in the in-memory NetBox (drafts): a change saved for a window is tried on the
copy, so its patches can be shown and approved while the real NetBox stays as it is.

The copy holds every object of the endpoints the platform uses, with the same ids, in the raw form
the in-memory NetBox stores (foreign keys as ids, choices as values); it renders them back as
NetBox does (orchestrator/memory_netbox.py).
"""

import itertools

from orchestrator import documents
from orchestrator.memory_netbox import FakeNetBox

# Parents before children, so a copy is complete at every step (the order is only for readers).
ENDPOINTS = (
    "dcim/manufacturers", "dcim/device-types", "dcim/device-roles", "dcim/sites", "dcim/locations",
    "dcim/racks", "ipam/rirs", "ipam/asns", "extras/tags", "extras/custom-fields", "ipam/vrfs",
    "ipam/prefixes", "dcim/devices", "dcim/interfaces", "ipam/ip-addresses", "dcim/cables", "vpn/l2vpns",
    documents.ENDPOINT,
)
# Rendered-only fields: the in-memory NetBox computes its own.
_DROP = {"url", "display", "display_url", "link_peers", "link_peers_type", "connected_endpoints",
         "connected_endpoints_type", "connected_endpoints_reachable", "assigned_object", "_occupied", "_depth"}


def _ref(value):
    """A nested object or choice as the in-memory NetBox stores it."""
    if isinstance(value, dict):
        if "value" in value and "label" in value:
            return value["value"]
        if "id" in value:
            return value["id"]
    return value


def raw(endpoint: str, obj: dict) -> dict:
    out = {}
    for key, value in obj.items():
        if key in _DROP:
            continue
        if key in ("data", "config_context", "local_context_data"):
            out[key] = value
        elif key == "custom_fields":
            out[key] = {k: _ref(v) for k, v in (value or {}).items()}
        elif key in ("a_terminations", "b_terminations"):
            out[key] = [{"object_type": t.get("object_type"), "object_id": t.get("object_id")} for t in value]
        elif isinstance(value, list):
            out[key] = [_ref(v) for v in value]
        else:
            out[key] = _ref(value)
    return out


async def clone(netbox) -> FakeNetBox:
    copy = FakeNetBox()
    top = 0
    for endpoint in ENDPOINTS:
        store = copy.objects.setdefault(endpoint, {})
        for obj in await netbox.find(endpoint):
            store[obj["id"]] = raw(endpoint, obj)
            top = max(top, obj["id"])
    copy._next_id = itertools.count(top + 1)   # new objects never take an id NetBox already uses
    copy.writes = 0
    return copy
