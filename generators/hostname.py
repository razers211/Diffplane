from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator


class Hostname(PartialGenerator):
    TAGS = ["system", "hostname"]

    def acl_huawei(self, _: Any) -> str:
        return """
        sysname
        """

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        yield "sysname", device.hostname
