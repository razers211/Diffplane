# Roadmap

## Available today

- Fabrics: symmetric (distributed gateways, border leaves) and centralized (a gateway pair of
  spines or border leaves); M-LAG pairs and lone leaves; OSPF underlay with optional keychain
  authentication; iBGP EVPN with spines as route reflectors.
- Intent from the GUI: segments and port attachments, Eth-Trunks, firewall and ISP services,
  routing intent (BGP peers, policies, prefix lists, filters, aggregates, static routes, OSPF
  processes) with automatic import of hand-made objects, IPv6 dual-stack, DHCP relay,
  microsegmentation (EPGs and contracts), QoS marks, port settings, edge-port protection, SNMPv3,
  syslog, NetStream, switch login over RADIUS.
- Lifecycle: new fabrics, adding and removing switches on a map, importing an existing fabric
  (with overrides for values that differ), zero-touch provisioning, software upgrades,
  DC interconnect (EVPN re-origination between two fabrics' border pairs).
- Safe change: diffs, approvals, pair-safe deploy waves, per-switch verification, drift checks,
  pre-deploy checks, change windows with staged drafts, intent rollback, config backups.
- Read path: topology with LLDP checks, traffic and capacity forecasts, health scores and a
  timeline, control-plane state, hardware and optics, anomaly detection, flows, logs,
  reachability ("why can't A reach B?"), a NOC wall view.
- Platform: roles with per-fabric limits, Active Directory and RADIUS sign-in, alerting by email
  or webhook, high availability on two servers and a witness.
- Automation: typed answers on the main API routes, a `diffplane` command-line client for CI,
  and a fabric's intent exported as a YAML file to keep in git and imported back as checked
  intent changes ([Command line](docs/cli.md)).

## Needs verification on more hardware

Everything has been tested on a single lab switch. Help is welcome with:

- M-LAG negotiation and EVPN between real leaf pairs, forwarding across the fabric;
- two gateway spines in a centralized fabric (whether they need a BGP session with each other);
- DC interconnect traffic between two real sites;
- the V300 zero-touch provisioning boot;
- more switch models (`seed/ports.py`) and software releases;
- queueing under load and NetStream on Eth-Trunk links.

## Code redesign

In order, one step at a time, each with the full test suite passing and nothing visible changing
for users.

1. **API split by area (done).** `orchestrator/app.py` holds start-up and middleware;
   `orchestrator/deps.py` the shared dependencies; `orchestrator/routers/` the routes, in their
   original order.
2. **Intent in the platform database (done).** Fabric settings, segments, routing, overrides
   and DC links were JSON documents kept only in NetBox config contexts. The platform database
   is now their source, every version kept (`intent_documents`); a write is recorded there
   first, then published to NetBox, whose contexts are the copy the generators read. An install
   from before is adopted at its first start. Code names documents (`orchestrator/documents.py`);
   the audit trail lists each version with its author, and a rollback point records the versions
   it was taken at.
3. **Annet as a library (done).** A pool of worker processes loads Annet once and calls its
   command functions, results handed back as data instead of parsed from its output; a stuck
   run is killed with its gnetcli server. Annet stays pinned; a test runs each command with it.
4. **One typed API client for the GUI (done).** Every call names its route as the orchestrator's
   OpenAPI schema does, with types generated from it (openapi-typescript), so a changed or removed
   endpoint, or a method it does not take, fails the type check. The main routes' answers (fabrics,
   switches, segments, services, diff and deploy jobs, drift, the signed-in account) have response
   models (`orchestrator/answers.py`), so their GUI types are generated too; the rest are typed by
   hand, a model added when a route's answer is worth a contract.
5. **Read-path services in Go.** The collector, the syslog receiver, the flow aggregator and the
   image server are small, I/O-bound and standalone; Go fits them (gnetcli, GoFlow2 and
   Prometheus are Go already) and builds them to small static binaries. Each one is replaced
   alone, behind the same metrics, tables and ports, and run side by side with the Python one
   before the switch. Measure the memory and CPU it saves first: if the gain is small, skip it.
   The orchestrator, the generators and the rulebook stay Python: Annet is a Python library.

   **Measured (2026-11-13, not done for now).** Five minutes of `docker stats` every 10 s on a
   test install, the collector polling one switch (counters every 5 s, state, control plane and
   hardware every 25-50 s), no syslog or flow traffic:

   | Service | CPU avg (max) | Memory avg | Process RSS |
   |---|---|---|---|
   | collector | 1.4 % (4.4 %) | 40 MiB | 60 MiB |
   | syslog | 0.04 % (0.7 %) | 45 MiB | 57 MiB |
   | flows (Python, beside GoFlow2's 15 MiB) | 0.7 % (5.3 %) | 53 MiB | 61 MiB |
   | sftpd | 0 % | 24 MiB | 41 MiB |

   About 160 MiB and 2 % of one core in all; a Go version might save some 100 MiB, so it is not
   worth a rewrite now. Measure again before deciding when it matters: the collector polling a
   real fabric (its cost grows with each switch), the flow aggregator and syslog receiver under
   real traffic.

## Second vendor: Arista EOS

Chosen after the [design note](docs/design/second-vendor.md): Arista EOS first, the first-version
scope listed there, a fabric is one vendor. Steps, in order, each with the full test suite passing
and every Huawei golden unchanged:

1. **Interface names per vendor.** NetBox keeps each switch's own interface names (EOS:
   `Loopback0`, `Port-Channel<n>`, `Ethernet<n>`, `Management1`, `Vxlan1`, `Vlan<n>`). One
   module (`generators/naming.py`) builds and parses them per vendor, and the generators, the
   seed, the fabric builder and the orchestrator ask it instead of writing VRP names.
2. **Vendor per fabric.** A fabric setting (`huawei` by default), the switch models offered
   limited to it, a port layout for vEOS-lab; Annet takes a model named `Arista ...` as EOS.
3. **Arista generators and goldens** for the first-version scope: underlay, EVPN with spines as
   route reflectors, MLAG pairs with a shared VTEP, segments (VLAN-to-VNI) on ports and
   port-channels, anycast gateways in one VRF per routed segment, NTP, SNMP, syslog.
4. **Our rules on Annet's Arista rulebook**, each with a patch test (undo forms, order).
5. **Deploys:** EOS config sessions; verification as today (empty diff, a fresh poll, links up).
6. **Collector over gNMI/OpenConfig** for interface state, counters and LLDP.
7. **EVE-NG lab** (2 spines, 2 MLAG pairs, 2 hosts on vEOS-lab): each form checked on the console,
   then deploys and removals with an empty diff, and traffic between the pairs.

Until step 7, every EOS form is written from Arista's documentation and marked unverified.

## Ideas

- Speed and negotiation lists (`speed auto <list>`) on copper ports.
- NTP authentication.
- A public demo dataset with traffic for the read-path views.

Proposals are welcome as issues; see [CONTRIBUTING.md](CONTRIBUTING.md).
