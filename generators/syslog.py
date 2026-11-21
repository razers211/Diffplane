from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.netstream import MANAGEMENT_VPN, management_address

LEVEL = "warning"   # warnings and worse (severity 4 and up)
DEFAULT_PORT = 514  # V300 leaves the default port out of the stored line


def syslog(device: Any) -> dict:
    """The fabric's syslog setting (Fabric page): {"enabled", "host", "port", "owned"}."""
    return ((device.config_context or {}).get("fabric") or {}).get("syslog") or {}


class Syslog(PartialGenerator):
    """The platform's syslog loghost, per fabric from the Fabric page.

    Sent over MEth in `_management_vpn_` (only named as the path, never changed), from the switch's
    MEth address, warnings and worse. Written in the form V300 stores it:
    `source-ip` before `level`, the port only when not 514.

    Owns only the loghost lines of addresses the platform has written for this fabric (`owned`),
    so turning syslog off or moving it removes them; any other loghost is left alone.
    """

    TAGS = ["syslog"]

    def acl_huawei(self, device: Any) -> str:
        return "\n".join(f"info-center loghost {host} ~" for host in syslog(device).get("owned") or [])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        setting, source = syslog(device), management_address(device)
        if not setting.get("enabled") or not setting.get("host") or source is None:
            return
        port = () if int(setting.get("port") or DEFAULT_PORT) == DEFAULT_PORT else ("port", setting["port"])
        yield ("info-center loghost", setting["host"], "vpn-instance", MANAGEMENT_VPN, "source-ip", source,
               "level", LEVEL, *port)
