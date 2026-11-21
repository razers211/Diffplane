import hashlib
import os
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import fabric_settings, fabric_value
from generators.netstream import MANAGEMENT_VPN

GROUP = "FABRIC-RO"   # every reader's group: read-only, privacy (authentication and encryption)


def snmp_intent(device: Any) -> dict:
    """The fabric's SNMP intent (`fabric.snmp`): location, contact, `v2c` (default on), `readers`
    {name: {auth, priv}}, `traps` {name: {address, reader}}, `owned` {users, traps}."""
    return ((device.config_context or {}).get("fabric") or {}).get("snmp") or {}


class Snmp(PartialGenerator):
    """SNMP agent: v2c with a read-only community from the SNMP_COMMUNITY environment variable,
    and/or v3 readers with their trap targets.

    The community is a secret, so it never comes from NetBox or the repository.
    VRP stores it encrypted (`snmp-agent community read cipher ... alias <alias>`),
    so the community is named with an alias derived from its value and our rulebook
    compares communities by alias (rulebook/huawei/snmp.py). Changing SNMP_COMMUNITY
    changes the alias, which shows up as a diff.

    "v2c only" is written the way the lab switch displays it: v2c, plus v3 disabled; both are
    stored as `version v2c v3`. A v3 reader is a USM user in the read-only
    group FABRIC-RO; its passwords are typed by the password job (VRP asks for them
    interactively), so its `authentication-mode` and `privacy-mode` lines are never this
    generator's. Users and trap targets the platform made (`owned`) are its own; any other user,
    trap host and SNMP line is left alone. The listening lines exist only while the fabric has a
    reader, and are owned once it has had one, so they go with the last reader.
    """

    TAGS = ["system", "snmp"]

    def acl_huawei(self, device: Any) -> str:
        if not fabric_value(device, "snmp", True):
            return ""   # an imported fabric running SNMPv3, which the platform does not make: left alone
        intent = snmp_intent(device)
        owned = intent.get("owned") or {}
        users = sorted({*(intent.get("readers") or {}), *(owned.get("users") or [])})
        traps = sorted({*(intent.get("traps") or {}), *(owned.get("traps") or [])})
        return "\n".join([
            "snmp-agent$", "snmp-agent sys-info version ~", "snmp-agent sys-info location ~",
            "snmp-agent sys-info contact ~", "snmp-agent community read ~", f"snmp-agent group v3 {GROUP} ~",
            *(f"snmp-agent usm-user v3 {u}$\nsnmp-agent usm-user v3 {u} group ~" for u in users),
            *(f"snmp-agent target-host host-name {t} ~" for t in traps),
            *(["snmp-agent protocol source-interface ~", "snmp-agent protocol vpn-instance ~"] if users else []),
        ])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not fabric_value(device, "snmp", True):
            return
        settings, intent = fabric_settings(device), snmp_intent(device)
        v2c, readers = intent.get("v2c", True), intent.get("readers") or {}
        yield "snmp-agent"
        if v2c and readers:
            yield "snmp-agent sys-info version v2c v3"
        elif v2c:
            yield "snmp-agent sys-info version v2c"
            yield "snmp-agent sys-info version v3 disable"
        else:
            yield "snmp-agent sys-info version v3"   # an imported fabric's v3-only form; a test switch's unverified
        if settings.snmp_location:
            yield "snmp-agent sys-info location", settings.snmp_location
        if settings.snmp_contact:
            yield "snmp-agent sys-info contact", settings.snmp_contact
        if v2c and (community := os.environ.get("SNMP_COMMUNITY")):
            yield "snmp-agent community read", community, "alias", community_alias(community)
        if readers:   # VRP answers SNMP on no interface until told; only references the management VPN
            yield "snmp-agent protocol source-interface MEth0/0/0"
            yield "snmp-agent protocol vpn-instance", MANAGEMENT_VPN
            yield "snmp-agent group v3", GROUP, "privacy notify-view ViewDefault"   # read view: the default
        for user in sorted(readers):
            yield "snmp-agent usm-user v3", user
            yield "snmp-agent usm-user v3", user, "group", GROUP
        for name, trap in sorted((intent.get("traps") or {}).items()):
            if trap.get("reader") in readers:
                yield ("snmp-agent target-host host-name", name, "trap address udp-domain", trap["address"],
                       "vpn-instance", MANAGEMENT_VPN, "params securityname", trap["reader"], "v3 privacy")


def community_alias(community: str) -> str:
    """A stable alias for a community: changes when the community changes, does not reveal it."""
    return "ro-" + hashlib.sha256(community.encode()).hexdigest()[:8]
