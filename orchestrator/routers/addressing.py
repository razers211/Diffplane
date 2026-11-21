"""Segment routing, DHCP relay, IPv6 and the fabric's address pools."""

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from orchestrator import routing as rt
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient
from orchestrator.deps import current_user, get_accounts, get_netbox, get_routing, INTENT_WRITE, _segment_call

router = APIRouter()


class RoutingIntent(BaseModel):
    routed: bool
    prefix_length: int = 24
    # A service name, "default" (the fabric's first service) or null for no default route.
    # A model, not Body() parameters: FastAPI reads an explicit null Body() as "use the default".
    firewall: str | None = "default"
    peers: list[int] = []


@router.put("/segments/{site}/{vni}/routing", dependencies=INTENT_WRITE)
async def set_routing(
    site: str, vni: int, intent: RoutingIntent,
    user: str = Depends(current_user), routing: rt.Routing = Depends(get_routing),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    """Route a segment (gateway subnet from the fabric pool, firewall default route and/or
    peerings) or stop routing it."""
    detail = (f"{site} vni={vni} routed firewall={intent.firewall} peers={intent.peers}" if intent.routed
              else f"{site} vni={vni} not routed")
    call = routing.set_routing(site, vni, intent.routed, intent.prefix_length, intent.firewall, intent.peers)
    return await _segment_call(call, accounts, user, "segment.routing", detail)


class RelayServer(BaseModel):
    address: str
    service: str | None = None   # the server sits behind this service (its VPN instance)
    segment: int | None = None   # ...or in this peered segment (VNI); neither: the segment's own


class RelayIntent(BaseModel):
    servers: list[RelayServer] = []   # empty: relay off


@router.put("/segments/{site}/{vni}/relay", dependencies=INTENT_WRITE)
async def set_relay(site: str, vni: int, intent: RelayIntent, user: str = Depends(current_user),
                    netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """DHCP relay of a routed segment: up to 8 servers; each gateway leaf relays from its
    own loopback (from the fabric's relay pool) with Option 82 link-selection."""
    from orchestrator import dhcp_relay

    servers = [s.model_dump(exclude_none=True) for s in intent.servers]
    detail = f"{site} vni={vni} servers={','.join(s['address'] for s in servers) or 'off'}"
    return await _segment_call(dhcp_relay.set_relay(netbox, site, vni, servers), accounts, user, "segment.relay", detail)


class Ipv6Intent(BaseModel):
    enabled: bool
    hosts: str = "slaac"                 # slaac, dhcpv6 or none
    servers: list[RelayServer] = []      # DHCPv6 servers (hosts dhcpv6), placed as DHCP relay servers


@router.put("/segments/{site}/{vni}/ipv6", dependencies=INTENT_WRITE)
async def set_segment_ipv6(site: str, vni: int, intent: Ipv6Intent, user: str = Depends(current_user),
                           netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """IPv6 of a routed segment: its /64 from the fabric's IPv6 pool, gateway ::1, and how
    its hosts get addresses (SLAAC, DHCPv6 relayed to its servers, or none)."""
    from orchestrator import ipv6

    servers = [s.model_dump(exclude_none=True) for s in intent.servers]
    detail = f"{site} vni={vni} " + (f"on hosts={intent.hosts} servers={','.join(s['address'] for s in servers) or 'none'}"
                                     if intent.enabled else "off")
    return await _segment_call(ipv6.set_ipv6(netbox, site, vni, intent.enabled, intent.hosts, servers), accounts, user,
                               "segment.ipv6", detail)


@router.put("/fabrics/{site}/ipv6-pool", dependencies=INTENT_WRITE)
async def set_ipv6_pool(site: str, prefix: str = Body(embed=True), user: str = Depends(current_user),
                        netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """The fabric's IPv6 pool: a /64 per segment; its first /64 for relay loopbacks."""
    from orchestrator import ipv6

    return await _segment_call(ipv6.set_pool(netbox, site, prefix), accounts, user, "fabric.ipv6-pool", f"{site} {prefix}")


@router.put("/fabrics/{site}/pip-pool", dependencies=INTENT_WRITE)
async def set_pip_pool(site: str, prefix: str = Body(embed=True), user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Move the fabric's M-LAG pairs to the PIP layout: own PIPs on LoopBack1, VTEPs on LoopBack2."""
    from orchestrator import pip_pool

    return await _segment_call(pip_pool.set_pool(netbox, site, prefix), accounts, user, "fabric.pip-pool", f"{site} {prefix}")


@router.put("/fabrics/{site}/relay-pool", dependencies=INTENT_WRITE)
async def set_relay_pool(site: str, prefix: str = Body(embed=True), user: str = Depends(current_user),
                         netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """The fabric's relay loopback pool, in its address namespace."""
    from orchestrator import dhcp_relay

    return await _segment_call(dhcp_relay.set_pool(netbox, site, prefix), accounts, user, "fabric.relay-pool", f"{site} {prefix}")
