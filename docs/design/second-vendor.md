# A second switch vendor: design note

Status: proposal, no code. It compares two candidates, Arista EOS and Cisco Nexus (NX-OS), says
what Diffplane would have to change for either, and recommends an order. The go or no-go is a
separate decision.

## What stays and what changes

Diffplane is two halves. The **vendor-neutral half** is most of the platform:
- intent in the platform database and NetBox;
- the GUI, roles, approvals, change windows, drafts, rollback and the audit trail;
- the deploy pipeline (diff, patch, approval, waves, per-switch verification);
- health, capacity and anomaly views over Prometheus and ClickHouse;
- the CLI and intent files.

The **Huawei half** is where every line a switch sees, or every value read from one, is
written:

| Part | Size today | What is Huawei-specific |
|---|---|---|
| Generators (`generators/`) | 39 files, 3,600 lines | every generator has only `acl_huawei` / `run_huawei`; `fabric.py` (750 lines) derives the fabric model from NetBox and is mostly neutral, but uses VRP names (LoopBack0/1/2, Nve1, Vbdif, VPN instance names) |
| Rulebook (`rulebook/`) | 14 files, 780 lines | VRP undo forms, ordering and prompts, on top of Annet's own `huawei.rul` (550 lines) |
| Golden configs (`tests/golden/`) | 63 files | VRP text |
| Collector (`collector/`) | 1,900 lines | NETCONF subtrees of 32 `huawei-*` YANG modules; `display` text through huawei-cli |
| Orchestrator, read on demand | 8 modules | `huawei-*` YANG (routes, trace, microsegmentation state, inventory, OSPF keychains, SNMPv3 users) |
| Orchestrator, commands sent | 12 modules | VRP CLI: `commit trial`, `save`, software upgrades, SFTP downloads, the import's `display` list, ZTP first-boot config, SNMPv3 passwords |
| Switch models (`seed/ports.py`) | CE68xx only | port names and layouts; Diffplane already learns a new model's ports from the switch over NETCONF |

The design choice that makes a second vendor possible already exists: Annet generators take one
method per vendor (`run_huawei`, `run_arista`, ...), and Annet picks it from the device's NetBox
manufacturer and model. So the generators can stay one per concern, gain a second
`run_*`/`acl_*` pair each, and keep sharing `fabric.py`'s derivations, once its VRP names move
to the Huawei methods.

## The candidates

### What Annet and gnetcli already have

Checked in the pinned versions (Annet 4.6.14, gnetcli_adapter 2.8.5, gnetcli v1.3.17):

| | Annet vendor | Annet rulebook (`.rul` lines; `.order`, `.deploy`) | Annet rule modules | gnetcli driver |
|---|---|---|---|---|
| Huawei (today) | `Huawei` | 551; both | aaa, bgp, iface, misc, portsplit, vlandb | `huawei` |
| Arista EOS | `Arista` | 86; both | aaa, iface, vlandb | `arista` (breed `eos4`) |
| Cisco Nexus | `Cisco.Nexus` | 87; both | iface | `nxos` (the adapter passes the breed through; unused here) |

Neither rulebook is close to Huawei's in depth. Each needs our own rules for the undo forms,
orders and prompts the fabric uses, as Huawei's did (`rulebook/`), each with a patch test.

### How each maps to Diffplane's fabric

| Diffplane concept | Huawei VRP (today) | Arista EOS | Cisco NX-OS |
|---|---|---|---|
| Leaf pair | M-LAG (DFS group, peer-link) | MLAG (peer-link, its own peer address) | vPC (peer-link, peer-keepalive) |
| Segment | bridge domain + VNI | VLAN + `vxlan vlan <v> vni <n>` | VLAN + `vn-segment`, `member vni` on nve1 |
| VTEP | Nve1, shared VTEP on LoopBack1 | `interface Vxlan1`, MLAG shared VTEP | `interface nve1`, vPC anycast VTEP |
| Segment gateway | VBDIF, anycast, one VPN instance per segment | SVI with `ip address virtual`, a VRF | SVI with `fabric forwarding mode anycast-gateway`, a VRF |
| Safe commit | two-stage commit, `commit trial` | config sessions, `commit timer` | `configure replace` with a commit timeout, checkpoints |
| Reads | NETCONF `huawei-*` YANG | gNMI/OpenConfig, eAPI (JSON) | gNMI/OpenConfig, NX-API, NETCONF |
| Flows | NetStream | sFlow | NetFlow / sFlow |

Every row is a known feature of each vendor, but none of it has been tried with Diffplane.
Which forms each vendor stores after a commit, and which questions it asks, would be checked
on the console first, as was done for VRP.

Both pairs are close to Diffplane's: a peer-link, a shared (anycast) VTEP and the two switches
deployed in different waves. A segment is a VLAN mapped to a VNI rather than a bridge domain,
so attachments become access or trunk VLANs on ports instead of L2 sub-interfaces.

## Lab

Tests run in **EVE-NG**. Images to check on the EVE-NG install before choosing:
- **Arista vEOS-lab:** free with an Arista account;
- **Cisco Nexus 9000v:** a Cisco download, usually needing an entitlement; basic routing runs
  without a licence, some features ask for one.

Each needs a nested-virtualisation host and its own vCPU and memory. A useful first topology is
two spines, two leaf pairs and two hosts. That is also the multi-switch test the Huawei side has
never had.

Virtual images prove config forms and control-plane behaviour, never ASIC forwarding,
queueing or hardware sampling (eNSP's CE images showed the same limit for Huawei).

## Scope of a first version

Only what a fabric needs to carry traffic:
- underlay (loopbacks, routed fabric links, OSPF);
- the EVPN overlay with spines as route reflectors;
- the leaf pair;
- segments with port attachments, routed segments with their anycast gateway;
- NTP, SNMP, syslog;
- diff, deploy with the vendor's safe commit, per-switch verification;
- a collector for interface state, counters and LLDP (OpenConfig over gNMI, which both offer,
  so one collector can serve both).

Left for later, each as its own step:
- firewall and ISP services;
- routing intent and its auto-import;
- microsegmentation (there is no common model across vendors);
- QoS, port protection, port settings;
- DHCP relay, IPv6, DC links;
- NetStream (sFlow instead);
- importing an existing fabric, ZTP, software upgrades, RADIUS login.

A fabric is one vendor. Mixed fabrics (Huawei spines, other leaves) are out of scope.

## Rough estimate (one vendor, first version)

Not measured: a guess from the Huawei work, for one engineer.

| Work | Weeks |
|---|---|
| Move VRP names out of `fabric.py`; the vendor picked per fabric | 1 |
| Generators and goldens for the first-version scope | 2-3 |
| Our rules on Annet's rulebook, each with a patch test | 1-2 |
| Safe commit and verification in the deploy runner | 1 |
| Collector over gNMI/OpenConfig (state, counters, LLDP) | 1-2 |
| EVE-NG lab: console rounds, then deploys and removals with an empty diff | 1-2 |
| **Total** | **7-11** |

The second of the two would cost less: the neutral `fabric.py`, the vendor choice, the gNMI
collector and the lab are done once.

## Recommendation

1. **Arista EOS first.** Its MLAG pair is close to Diffplane's, and its CLI is line-based like
   VRP, so Annet's line patching fits. Annet has its rulebook with order and deploy files,
   gnetcli has its driver, and vEOS-lab is free to download.
2. **Cisco Nexus second.** vPC maps to the pair just as well, but the image needs a Cisco
   entitlement, and gnetcli's `nxos` driver has not been used through Annet's adapter.

## Open questions for the decision

- Which of the two do the people asking for a second vendor actually run?
- Is a lab of it possible on the EVE-NG host (images, licences, CPU and memory)?
- Is a first version without services, routing intent and microsegmentation useful to them?
