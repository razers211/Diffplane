"""Annet generators for the Huawei CloudEngine fabric. One module per concern."""

from annet.generators import BaseGenerator
from annet.storage import Storage

from generators import (
    bgp_evpn, bridge_domains, fabric_links, firewall_bgp, firewall_services, gateways, l2_subinterfaces, tenant_bgp, tenant_vrfs,
)
from generators import hostname, loopbacks, mlag, mlag_bypass, ntp, nve, ospf, snmp, vlans
from generators import bgp_aggregates, bgp_peers, ospf_processes, route_policies, routing_filters, static_routes, vlanifs
from generators import dci, dhcp_relay, microsegmentation, netstream, ospf_auth, port_profiles, switch_login, protection, qos, segment_peering, sftp_key, syslog, trunks


def get_generators(store: Storage) -> list[BaseGenerator]:
    return [
        hostname.Hostname(store),
        ntp.Ntp(store),
        snmp.Snmp(store),
        loopbacks.Loopbacks(store),
        fabric_links.FabricLinks(store),
        ospf_auth.OspfKeychain(store),   # only while the fabric has OSPF authentication
        switch_login.SwitchLogin(store),   # only while the fabric has switch login; goes under commit trial
        ospf.Ospf(store),
        mlag.Mlag(store),
        trunks.Trunks(store),
        mlag_bypass.MlagBypass(store),
        vlans.Vlans(store),
        nve.Nve(store),
        bgp_evpn.BgpEvpn(store),
        bridge_domains.BridgeDomains(store),
        l2_subinterfaces.L2Subinterfaces(store),
        tenant_vrfs.TenantVrfs(store),
        gateways.Gateways(store),
        dhcp_relay.DhcpRelay(store),   # dhcp enable and the relay loopbacks (VBDIF lines: Gateways)
        tenant_bgp.TenantBgp(store),
        vlanifs.Vlanifs(store),
        firewall_services.FirewallServices(store),
        segment_peering.SegmentPeering(store),
        firewall_bgp.FirewallBgp(store),
        # General routing intent.
        routing_filters.RoutingFilters(store),
        route_policies.RoutePolicies(store),
        static_routes.StaticRoutes(store),
        bgp_peers.BgpPeers(store),
        bgp_aggregates.BgpAggregates(store),
        ospf_processes.OspfProcesses(store),
        netstream.NetStream(store),   # only while the fabric's NetStream is on
        syslog.Syslog(store),   # only the platform's own loghost, while the fabric's syslog is on
        sftp_key.SftpServerKey(store),   # the image server's host key, once SFTP_ADDRESS is set
        microsegmentation.Microsegmentation(store),   # only while the fabric's mode is learn or enforce
        qos.Qos(store),   # only while the fabric's QoS is on
        dci.Dci(store),   # only on a DC link's border pair
        port_profiles.PortProfiles(store),   # only while the fabric's port settings are managed
        protection.PortProtection(store),   # from the first time the fabric left Off
    ]
