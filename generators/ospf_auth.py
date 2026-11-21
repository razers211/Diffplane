import json
import os
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import fabric_settings

KEYCHAIN = "fabric-ospf"   # V300 lowers keychain names
ALGORITHM = "hmac-sha-256"
# When a key starts to be accepted. A fixed past date: rolls never depend on a switch's clock.
RECEIVE_FROM = "00:00 2020-01-01"


class MissingOspfKey(Exception):
    pass


def ospf_auth(device: Any) -> dict:
    """The fabric's OSPF authentication (`fabric.ospf_auth`): `site`, `enabled`, `keys` [key IDs],
    `send` (the ID switches send with). Kept with `enabled` false once on, so the removal is owned."""
    return ((device.config_context or {}).get("fabric") or {}).get("ospf_auth") or {}


class OspfKeychain(PartialGenerator):
    """The keychain OSPF authenticates fabric links with.

    Keys are random, kept in the platform database and given to each run in OSPF_KEYS, never in
    NetBox. A roll is three deploys with no clock involved: a new key accepted everywhere, then
    `default send-key-id` moved to it, then the old key removed. V300 refuses overlapping send
    times, so keys have no send time. The Ospf generator writes each link's line.
    """

    TAGS = ["underlay", "ospf"]

    def acl_huawei(self, device: Any) -> str:
        return f"keychain {KEYCHAIN} ~\n    ~ %global" if ospf_auth(device) else ""

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        auth = ospf_auth(device)
        if not auth.get("enabled"):
            return
        fabric_settings(device)   # fails on a missing fabric context, as every generator
        stored = json.loads(os.environ.get("OSPF_KEYS") or "{}").get(auth["site"]) or {}
        with self.block("keychain", KEYCHAIN, "mode absolute"):
            for key_id in auth["keys"]:
                if str(key_id) not in stored:
                    raise MissingOspfKey(f"OSPF key {key_id} of {auth['site']} is not in OSPF_KEYS: "
                                         "only the orchestrator's runs have the keys")
                with self.block("key-id", key_id):
                    yield "algorithm", ALGORITHM
                    yield "key-string cipher", stored[str(key_id)]
                    yield "receive-time", RECEIVE_FROM, "duration infinite"
                    if key_id == auth["send"]:
                        yield "default send-key-id"
