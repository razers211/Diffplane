from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import all_trunks, device_value, local_services
from generators.port_profiles import server_ports

# The templates: storm limits in percent of the port's speed, BPDU guard
# (edge port; the global `stp bpdu-protection` shuts an edge port receiving a BPDU) and loop
# detection; a shut port reopens after 45 s. Hypervisor: no edge port (virtual switches may send BPDUs).
STORM = {"broadcast": 1, "multicast": 1, "unknown-unicast": 5}
TEMPLATES = {"server": {"edge": True}, "hypervisor": {"edge": False}}
RECOVERY = 45
CAUSES = ("bpdu-protection", "loopback-detect", "storm-control")
MODES = ("off", "watch", "on")


def document(device: Any) -> dict:
    return ((device.config_context or {}).get("fabric") or {}).get("protection") or {}


def mode(device: Any) -> str:
    return document(device).get("mode") or "off"


def edge_ports(device: Any) -> tuple[list[Any], list[Any]]:
    """(physical edge ports, edge Eth-Trunks): server ports and server trunks, never a firewall's or
    ISP's port or trunk (a service's, or a VLAN trunk carrying services: an imported fabric's spines)."""
    physical, trunks = server_ports(device)
    services = {s.port for s in local_services(device) if s.port}
    vlan_trunks = set(device_value(device, "vlan_trunks", {}) or {})
    keep = lambda i: i.name not in services | vlan_trunks   # noqa: E731
    return [i for i in physical if keep(i)], [i for i in trunks if keep(i)]


class PortProtection(PartialGenerator):
    """Edge-port protection: a template per server port or server trunk,
    per fabric Off / Watch / On (Network › Port protection).

    On: storm control on the physical port, or on every member of a trunk (V300 has none on an
    Eth-Trunk), with action `block`, log and trap; `stp edged-port enable` (Server template) and
    `loopback-detect enable` with action `error-down` on the port or the trunk itself (none on a
    member); globally `stp bpdu-protection` and error-down auto-recovery after 45 s for the three
    causes. Watch: the same limits with log and trap and no action, loop detection's default action
    (alarm), no BPDU protection or recovery: nothing is ever shut. Forms from console
    tests.

    Owns `storm control`, `stp edged-port` and `loopback-detect` on edge ports and edge trunks' members
    by kind, and the global lines exactly, from the first time the fabric left Off (`owned`, never
    cleared): before that hand-made protection is left alone, afterwards Off removes it all.
    """

    TAGS = ["protection"]

    def acl_huawei(self, device: Any) -> str:
        if not document(device).get("owned"):
            return ""
        physical, trunks = edge_ports(device)
        members = {m for t in all_trunks(device) if t.name in {i.name for i in trunks} for m in t.members}
        lines = ["stp bpdu-protection", *(f"error-down auto-recovery cause {c} ~" for c in CAUSES)]
        for name in sorted({i.name for i in physical} | members):
            lines += [f"interface {name} %cant_delete=1", "    storm control ~"]
            if name not in members:
                lines += ["    stp edged-port ~", "    loopback-detect ~"]
        for trunk in trunks:
            lines += [f"interface {trunk.name} %cant_delete=1", "    stp edged-port ~", "    loopback-detect ~"]
        return "\n".join(lines)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        current = mode(device)
        if current not in ("watch", "on"):
            return
        wanted = (document(device).get("ports") or {}).get(device.name) or {}
        physical, trunks = edge_ports(device)
        members = {t.name: t.members for t in all_trunks(device)}
        protected = [(i.name, wanted[i.name]) for i in physical + trunks if wanted.get(i.name) in TEMPLATES]
        if current == "on" and protected:
            yield "stp bpdu-protection"
            for cause in CAUSES:
                yield "error-down auto-recovery cause", cause, "interval", RECOVERY
        for name, template in protected:
            for member in members.get(name) or ():   # a trunk's storm limits go on its members
                with self.block("interface", member):
                    yield from self._storm(current)
            with self.block("interface", name):
                if name not in members:
                    yield from self._storm(current)
                if TEMPLATES[template]["edge"]:
                    yield "stp edged-port enable"
                yield "loopback-detect enable"
                if current == "on":
                    yield "loopback-detect action error-down"
                    yield "loopback-detect recovery-time", RECOVERY

    @staticmethod
    def _storm(current: str) -> Iterator[str | tuple]:
        for kind, percent in STORM.items():
            yield "storm control", kind, "min-rate percent", percent, "max-rate percent", percent
        if current == "on":
            yield "storm control action block"
        yield "storm control enable log"
        yield "storm control enable trap"
