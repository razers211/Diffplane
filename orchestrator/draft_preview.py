"""Draft preview: the patch each switch of a fabric would get with a change set applied,
computed on the change set's copy of NetBox (orchestrator/drafts.py), nothing written anywhere.

It runs in its own process (`python -m orchestrator.draft_preview`, request as JSON on stdin,
result on stdout) because Annet's hardware and rulebook providers can be set only once per
process; the request holds running configs (secrets) and travels only through the pipe.
"""

import json
import sys

from orchestrator.memory_netbox import FakeNetBox


def preview(request: dict) -> dict:
    """{"site", "objects": {endpoint: {id: object}}, "configs": {switch: running config}}
    -> {"patches": {switch: patch, as `annet patch` prints it}}"""
    from orchestrator.annet_view import devices_from_netbox, patch_to

    nb = FakeNetBox()
    nb.objects = {endpoint: {int(i): o for i, o in objects.items()} for endpoint, objects in request["objects"].items()}
    configs = request["configs"]
    patches = {}
    for device in devices_from_netbox(nb, request["site"]):
        if device.name in configs:
            patch = patch_to(configs[device.name], device, "  ")
            patches[device.name] = patch.strip("\n") + "\n" if patch.strip() else ""   # as AnnetRunner.patch
    return {"patches": patches}


def main() -> int:
    from annet import hardware, rulebook

    hardware.hardware_connector.set(hardware.AnnetHardwareProvider)
    rulebook.rulebook_provider_connector.set(rulebook.DefaultRulebookProvider)
    json.dump(preview(json.load(sys.stdin)), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
