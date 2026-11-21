# Supported switches

Diffplane generates configuration for Huawei CloudEngine data-centre switches running VRP V300
(YunShan OS). It is an independent project, not affiliated with or endorsed by Huawei. Huawei,
CloudEngine and VRP are trademarks of Huawei Technologies Co., Ltd.; they are used only to say
what Diffplane is compatible with.

The CE6800, CE8800 and CE9800 series run the same software with the same syntax; only their port
names and numbers differ. Annet gives every Huawei CE model its `vrp85` breed and Diffplane's
rulebook (`rulebook/`).

## Tested

| Model | Software | What was tested |
|---|---|---|
| CE6881H-48S6CQ | V300R023C00SPC500, V300R025C00SPC500 | every generator, deployed and removed on a single lab switch with an empty diff afterwards; NETCONF reads of the collector |

Microsegmentation needs V300R025 (V300R023 has no such module). `check file-digest` (used by
software upgrades) is missing on V300R023; the upgrade job then checks the file size and runs
`check software` / `check patch` instead.

## Models and ports are read from the switch

Diffplane reads each switch's model, software release, patch, serial and front-panel ports over
NETCONF (`huawei-system` `system-info`, `huawei-devm` ports), when its host keys are pinned, every
hour, and from **Read from the switch** on its Settings tab. Each port gets a NetBox interface type
from its hardware type (`Eth10GEPort` is RJ45 copper, `Eth10GFPort` an SFP+ cage, `Eth100GFPort`
a QSFP28 cage, ...), which gives its speed and whether it takes copper speed settings.

In **New fabric** and **Add switches**, a switch that already answers is **detected** before it is
placed: its host keys are fetched, an approver compares and approves them (as in Import), and its
model and ports are read over NETCONF (read-only). It is then built with exactly those ports,
whatever its model, added active, and its approved keys are pinned. A switch with NETCONF off
cannot be detected.

A model's ports are kept as its NetBox device type's interface templates. So the first switch of a
new model is detected (or added by ZTP or import); after that, more switches of that model can be
planned before they exist. These models are known from the start:

| Model | Layout | Notes |
|---|---|---|
| CE6881H-48S6CQ | 10GE1/0/1-48 (SFP+), 100GE1/0/1-6 | |
| CE6881H-48T6CQ | 10GE1/0/1-48 (RJ45 copper), 100GE1/0/1-6 | |
| CE6881H-48T6CQ-K | as the -48T6CQ | **assumed**: an active switch of it blocks deploy approval until it has been read |

When a switch reports another model or other ports than NetBox has, NetBox is corrected, unless a
port that would disappear is in use (cabled, in a trunk, routed, carrying segments or a service,
named or turned off). Then nothing changes, the read names those ports, and deploy approval for
that switch is blocked until they are freed and the switch is read again.

Not handled yet: breakout ports (`100GE1/0/1:1`) and chassis switches with line cards.

## What is not verified

Everything vendor-specific that was not seen on a real switch is treated as unverified. In
particular, with only one lab switch:

- multi-device behaviour: M-LAG negotiation between two real switches, EVPN between VTEPs,
  forwarding between leaf pairs, the DCI re-origination between two fabrics;
- the V300 zero-touch provisioning boot itself (DHCP options read, intermediate file format);
- queueing under load, flow contents of NetStream under real traffic, a link at a fixed copper
  speed.

Bring a new fabric up in **diff-only** mode first (deploys off), read every diff, then deploy one
switch and one concern at a time. See [operations](operations.md#bringing-up-a-new-fabric-greenfield).

## Required on the switch

What Diffplane needs to reach a switch, configured by hand or by ZTP (never managed by
Diffplane):

- MEth0/0/0 in `_management_vpn_` with its address and a default route;
- SSH (`stelnet server enable`) and NETCONF over SSH on port 830 (`snetconf server enable`);
- a local account for Annet and one for the collector (read-only is enough for the collector);
- an RSA host key of at least 3072 bits.

See [operations](operations.md) for the exact lines.

## Versions of the tools

| Component | Version |
|---|---|
| Annet | 4.6.14 (rulebook behaviour depends on the exact version) |
| gnetcli_adapter | 2.8.5 |
| gnetcli server | v1.3.17, built with `deploy/gnetcli/known-hosts.patch` |
| NetBox | 4.4.10 |
