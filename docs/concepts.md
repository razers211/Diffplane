# Concepts

## Fabric

A fabric is one leaf-spine network, usually one data centre, stored as one NetBox site. It runs
OSPF (area 0) as underlay and iBGP EVPN in one fabric ASN as overlay, spines being route
reflectors. Leaves come in **M-LAG pairs** (a DFS group, a peer-link Eth-Trunk, a shared VTEP),
or alone (a *lone leaf*, with its own VTEP).

Everything that repeats per fabric is scoped to it: its addressing lives in its own NetBox VRF
(`fabric-<site>`), used only as an address namespace, so two fabrics can reuse the same values.
Device names are unique across all fabrics.

A fabric has an **architecture**:

- **symmetric** (default): every leaf routes; each routed segment gets a VPN instance with an L3
  VNI, and anycast gateways exist on the leaves where the segment is attached. **Border leaves**
  hold every routed segment's VPN instance and host firewall and ISP services.
- **centralized** (asymmetric IRB): one M-LAG pair (two spines or two border leaves) is the
  **gateway pair**. Every routed segment is stretched to it; the other leaves are layer 2 only.
  There is no L3 VNI; routing between segments happens on the gateway pair.

Switches are **planned** (modelled only, never contacted) or **active** (a management address on
MEth, host keys pinned). The GUI owns status and management addresses.

## Store intent, derive the rest

NetBox holds what an operator decided. Anything that can be computed is computed by a generator
instead of stored: loopbacks and point-to-point addresses come from pools (NetBox
"next available"), route targets are `<ASN>:<VNI>`, the L3 VNI is the L2 VNI + 1,000,000, the
DFS priority follows the switch names, sub-interface numbers follow the bridge domain.

## Segments

A segment is a VXLAN layer-2 network: a NetBox L2VPN with a **VNI** and a **bridge-domain ID**,
unique within its fabric. Ports carry segments **tagged** (a VLAN ID) or **untagged**; Diffplane
generates an L2 sub-interface per attachment (`<port>.<bd>` tagged, `<port>.1` untagged). An
attachment to an M-LAG server trunk is written on both leaves of the pair.

A segment can be **routed**: it gets a gateway subnet from the fabric's gateway pool, an anycast
gateway (VBDIF, the subnet's first host, one fabric-wide gateway MAC) and its own VPN instance,
named `<segment>-<bd>`. Segments reach each other in two ways, combinable:

- **peering**: both VPN instances import each other's route target (symmetric) or static routes
  between them on the gateway pair (centralized);
- **through a firewall service** (below): the segment's default route points at the firewall.

Optional per segment: IPv6 (a /64 from the fabric's IPv6 pool, SLAAC or DHCPv6), DHCP relay
(through a per-leaf relay loopback, with Option 82 link selection), a QoS mark.

## Services

A **service** connects routed segments to something outside the fabric:

- a **firewall service**: a dedicated port (or an M-LAG trunk of a pair) to a firewall, a VLAN, a
  Vlanif in the service's own VPN instance, eBGP to the firewall, and per segment two inter-VPN
  static routes (the segment's subnet towards the firewall's instance, a default route back);
- an **ISP service**: the same container (port, VLAN, Vlanif, VPN instance) with no automatic
  routing; its session to the ISP is defined in routing intent, with import and export policies.

## Routing intent

BGP peers, route-policies, prefix lists, AS-path and community filters, aggregates, static
routes and OSPF processes are kept as one validated JSON document per fabric. On an M-LAG pair,
intent is defined once and generated identically on both switches; each switch of the pair may
also have routing of its own, on it alone (for example each ISP link's BGP session on its own
border switch). An object in both must be identical.

Ownership differs from the rest of the configuration: Diffplane owns only the routing objects in
intent (and the ones deleted from intent until a verified deploy removed them). Routing objects
made by hand on a switch are **imported into intent automatically**, never removed.

The GUI opens routing in a simple view (connections, "we accept" / "we send", network lists, rule
sets) with an expert view behind a toggle.

## Ownership

A generator owns lines either **by kind** (every line of that kind on a switch is Diffplane's:
VPN instances except `_management_vpn_` and `_public_`, Vlanifs except the M-LAG bypass one,
front-panel `port link-type` / `port trunk allow-pass`, bridge domains, sub-interfaces, NVE
VNIs) or **exactly** (only the lines it writes). Anything made by hand of an owned kind shows up
as a removal in the diff. Diffplane **never manages the management plane**: MEth, the management
VPN instance and its default route, and AAA local users are never touched (see the
[security model](security-model.md) for the few opt-in exceptions).

## Import

An existing fabric can be imported instead of described. The operator gives the management
addresses; an approver compares and pins each switch's host keys; the switches are read with
`display` commands only (over NETCONF, or SSH when NETCONF is off); links come from LLDP. The
preview builds the fabric in memory and shows the patch the first deploy would send.

Values the switches use that differ from what Diffplane would derive are kept as **overrides**
(a catalogue of known keys in `orchestrator/overrides.py`: DFS settings, NVE MAC, OSPF timers,
per-segment route distinguishers, ...). Each can later be **made standard** from the Fabric page,
with a preview of the patch. Structural differences Diffplane cannot model are reported as
*misfits*, and the switch stays diff-only. An imported fabric starts with deploys off.

## Deploys and waves

A deploy is prepared for a fabric: every active switch's patch is computed, the pre-deploy
checks run (errors block approval: duplicate addresses, overlapping subnets, routing loops
between VPN instances, ...), and the job waits for an approver.

Approved, it rolls out in **waves**: switches deployed at once, never both switches of an M-LAG
pair, at most half of a fabric's active switches and half its spines. Each switch's patch is
recomputed right before it is sent and must equal the approved one. After each switch:
empty diff, a fresh collector poll, no fabric link that was up is down; then `save` and a config
backup. A failure starts no new wave and the job fails naming the switch.

While a deploy is approved or rolling out, intent is frozen (writes answer 409). A deploy
requested meanwhile is queued. Deploys can also wait for a **change window**: changes are saved
as a draft, previewed, approved, and applied at the window's start.

Deploys are refused for any fabric an admin has not turned deploys on for.

## Drift

Every 15 minutes Diffplane diffs every active switch against the config generated from intent.
A non-empty diff is "out of sync": either a change by hand on the switch, or intent saved but not
deployed. The GUI shows it in a bar on every page; Prometheus alerts after 5 minutes.

## Roles

`viewer` (read) < `operator` (change intent, run diffs, request deploys) < `approver` (approve
deploys, pin host keys) < `admin` (accounts, settings, per-fabric deploy permission). Viewers and
operators may be limited to chosen fabrics. Sign-in is local, or Active Directory (LDAPS) and
RADIUS with group mapping; local accounts always work.
