import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import (FRONT_PANEL_PORT, ip_with_mask, has_overlay, local_services, mlag_mac, mlag_trunks,
                               device_value, service_scheme, single_trunks, v6, v6_interface, vlan_list)
from generators.mlag_bypass import bypass_vlan


class Vlanifs(PartialGenerator):
    """Every Vlanif on a leaf but the M-LAG bypass one, and the L2 trunk settings that carry
    their VLANs: `port link-type` / `port trunk allow-pass` on front-panel ports and on server
    trunks (M-LAG, and single trunks since: a service there is its device's alone).

    Containers owned by kind, so a deleted service or VLAN interface is
    removed from the switch; hand-made ones show as removals. A service's Vlanif in its VPN
    instance and its dedicated port as a trunk. A service on an M-LAG pair uses the pair's
    server trunk and the M-LAG dual-active gateway: a main address per leaf, the shared M-LAG
    address and the same derived MAC on both leaves (for services only). The VLANs themselves are in `vlan batch`, owned by
    the Vlans generator. Never MEth.
    """

    TAGS = ["tenants", "vlanifs"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        bypass = bypass_vlan(device)
        vlanif = f"Vlanif(?!{bypass}$)\\d+" if bypass is not None else "Vlanif\\d+"
        # Server trunks (M-LAG and single): only their L2 trunk settings. Mlag or Trunks owns (and
        # alone may delete) the trunk and its other lines; the same row text here merges with theirs.
        trunks = [line for t in mlag_trunks(device) + single_trunks(device)
                  for line in (f"interface {t.name} %cant_delete=1", "    port link-type ~", "    port trunk allow-pass ~")]
        return "\n".join([
            f"interface {FRONT_PANEL_PORT}",
            "    port link-type ~",
            "    port trunk allow-pass ~",
            *trunks,
            f"interface */{vlanif}/  %cant_delete=0",
            "    ~ %global",
        ])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        services = local_services(device)
        carried: dict[str, list[int]] = {}
        for service in services:
            if service.port is not None:
                carried.setdefault(service.port, []).append(service.vlan)
        trunks = {t.name for t in mlag_trunks(device) + single_trunks(device)}
        # VLAN trunks as the switch has them (imported fabrics): {port: {"vlans": "2 to 4094", "undo_vlan1": true}}
        kept = device_value(device, "vlan_trunks", {}) or {}
        for port in sorted(carried.keys() | kept.keys()):
            if port.startswith("Eth-Trunk") and port not in trunks:
                raise ValueError(f"{device.name}: {port} is not a server trunk of this device")
            with self.block("interface", port):
                yield "port link-type trunk"
                if port in kept:
                    if kept[port].get("undo_vlan1"):
                        yield "undo port trunk allow-pass vlan 1"
                    yield "port trunk allow-pass vlan", kept[port]["vlans"]
                else:
                    yield "port trunk allow-pass vlan", vlan_list(carried[port])
        for service in services:
            scheme = service_scheme(device, service.name)   # an imported fabric's firewall contexts
            with self.block("interface", f"Vlanif{service.vlan}"):
                yield "ip binding vpn-instance", service.vrf
                if (main := service.main_address(device.name)) is None:
                    yield "ip address", ip_with_mask(service.address)
                    if scheme.get("arp_proxy"):
                        yield "arp proxy enable"
                else:   # on an M-LAG pair: the dual-active gateway
                    own, shared = (service.address, main) if scheme.get("shared_main") else (main, service.address)
                    yield "ip address", ip_with_mask(own)
                    if scheme.get("arp_proxy"):
                        yield "arp proxy enable"
                    yield "mac-address", scheme.get("mac") or mlag_mac(service.address.ip)
                    yield "m-lag ip address", ip_with_mask(shared)
                if service.ipv6 is not None:   
                    yield "ipv6 enable"
                    if (main6 := dict(service.ipv6.leaf_addresses).get(device.name)) is None:
                        yield "ipv6 address", v6_interface(service.ipv6.address)
                    else:   # on an M-LAG pair: this leaf's own address, the shared one without a length
                        yield "ipv6 address", v6_interface(ipaddress.IPv6Interface(main6))
                        yield "m-lag ipv6 address", v6(service.ipv6.address.ip)
