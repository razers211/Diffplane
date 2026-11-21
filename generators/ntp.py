from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import fabric_settings


class Ntp(PartialGenerator):
    """NTP client servers.

    Owns every `ntp unicast-server` line, so servers not in the fabric settings are removed;
    a fabric without servers owns none.
    With `ntp_vpn_instance` set (e.g. the management VRF) each server gets
    `vpn-instance <name>`. Verified on the lab switch (V300R023).
    """

    TAGS = ["system", "ntp"]

    def acl_huawei(self, device: Any) -> str:
        # A fabric without NTP servers manages no NTP at all.
        if not fabric_settings(device).ntp_servers:
            return ""
        return """
        ntp unicast-server ~
        """

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        settings = fabric_settings(device)
        for server in settings.ntp_servers:
            if settings.ntp_vpn_instance:
                yield "ntp unicast-server", server, "vpn-instance", settings.ntp_vpn_instance
            else:
                yield "ntp unicast-server", server
