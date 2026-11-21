from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import fabric_asn
from generators.dci import stretched
from generators.fabric import centralized, device_value, gateway_segments, has_vtep, is_gateway, not_management, port_segments, routed_segments, segment_scheme
from generators.nve import NVE
from generators.qos import bridge_domain_marks, policy


class BridgeDomains(PartialGenerator):
    """One bridge domain per segment used on the leaf, with its EVPN instance and NVE VNI.

    L2 route target = <fabric ASN>:<VNI>, imported and exported. The L3 route
    target (export only) is added with the tenant VPN instances (symmetric fabrics only).
    In a centralized fabric the gateway pair also has the bridge domain of every routed
    segment, attached there or not. A segment marked for QoS has the mark's traffic policy
    inbound (the policy itself is the Qos generator's). A DC link's border has every segment
    it stretches, with `vxlan vni <DCI VNI> split-group <group>` and that VNI on Nve1. Owns every bridge
    domain on the leaf, so one no longer in NetBox is removed. Format as on ref-leaf1 (V300).
    """

    TAGS = ["segments", "bridge-domains"]

    def acl_huawei(self, device: Any) -> str:
        if not has_vtep(device):
            return ""
        return "\n".join([f"bridge-domain */{not_management(device, 'bridge_domains')}\\d+/", "    ~ %global",
                          f"interface {NVE}", f"    vni */{not_management(device, 'vnis')}\\d+/ ~"])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_vtep(device):
            return
        segments = {s.vni: s.bd_id for s in port_segments(device)}
        # Centralized: every routed segment is stretched to the gateway pair.
        segments.update({s.vni: s.bd_id for s in gateway_segments(device)})
        # a DC link's border has every segment it stretches, with the mapped DCI VNI.
        dci = stretched(device)
        known = (device.config_context or {}).get("segments") or {}
        segments.update({vni: known[str(vni)]["bd_id"] for vni in dci if str(vni) in known})
        # bridge domains the switch has with no port (an imported fabric's leaves), and Nve VNIs without one
        kept = device_value(device, "segments_without_ports", []) or []
        segments.update({vni: known[str(vni)]["bd_id"] for vni in kept if str(vni) in known})
        kept_vnis = set(device_value(device, "kept_vnis", []) or [])
        if not segments and not kept_vnis:
            return
        asn = fabric_asn(device)
        # No L3 VNI in a centralized fabric: the L2 route target only (an imported fabric's gateways have one).
        routed = {vni: s for vni, s in routed_segments(device).items()
                  if s.has_l3(device) and (not centralized(device) or is_gateway(device))}
        gateways = {s.vni for s in gateway_segments(device)}
        marks = bridge_domain_marks(device)
        for vni, bd_id in sorted(segments.items(), key=lambda item: item[1]):
            scheme = segment_scheme(device, vni)
            l2 = scheme.get("l2_rt") or f"{asn}:{vni}"
            with self.block("bridge-domain", bd_id):
                yield "vxlan vni", vni
                if vni in dci:
                    yield "vxlan vni", dci[vni][0], "split-group", dci[vni][1]
                with self.block("evpn"):
                    yield "route-distinguisher auto"
                    yield "vpn-target", l2, "export-extcommunity"
                    yield "vpn-target", l2, "import-extcommunity"
                    if vni in routed:
                        # Design: the L3 route target is exported only, never imported.
                        yield "vpn-target", routed[vni].l3_rt(asn), "export-extcommunity"
                if scheme.get("bd_arp") and vni not in gateways:   # an imported fabric's layer-2 leaves
                    yield "arp l2-proxy enable"
                    yield "arp collect host enable"
                if bd_id in marks:
                    yield "traffic-policy", policy(marks[bd_id]), "inbound"
        with self.block("interface", NVE):
            for vni in sorted(segments.keys() | {d for vni, (d, _) in dci.items() if vni in segments} | kept_vnis):
                yield "vni", vni, "head-end peer-list protocol bgp"
