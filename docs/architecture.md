# Architecture

Diffplane manages a leaf-spine fabric (BGP EVPN, VXLAN, M-LAG) of CloudEngine-compatible
switches from one web GUI. Operators state **intent** (segments, ports, services, routing); the
platform turns it into device configuration, shows the exact patch, and deploys it only after
approval. The fabric runs its own control plane: if Diffplane is down, the network keeps
forwarding.

```
          people (browser, HTTPS)
                 │
          ┌──────▼───────┐        ┌───────────┐
          │     GUI      │        │ Postgres  │  users, jobs, approvals,
          │ React + TS   │        │ (platform)│  diff snapshots, audit
          └──────┬───────┘        └─────▲─────┘
                 │ /api, same origin    │
          ┌──────▼──────────────────────┴─────┐
          │           Orchestrator            │  the only writer to NetBox
          │   FastAPI: jobs, approvals,       │
          │   drift, checks, read-path views  │
          └──┬──────────────┬──────────────┬──┘
   intent    │              │ generate,    │ read
   (REST)    │              │ diff, patch, │ (PromQL)
      ┌──────▼─────┐  ┌─────▼──────┐  ┌────▼───────┐
      │  NetBox    │  │   Annet    │  │ Prometheus │◄── collector (NETCONF, read-only)
      │ (headless) │◄─┤ generators │  └────────────┘          ▲
      └────────────┘  └─────┬──────┘                          │
                            │ gnetcli (SSH, pinned host keys) │
                      ┌─────▼─────────────────────────────────┴──┐
                      │                 switches                 │
                      └──────────────────────────────────────────┘
```

## Write path: intent to device

1. **GUI** (`gui/`): intent forms, the patch review, approval and deploy, the topology map. It
   is built into the orchestrator image and served at `/ui/`; it only ever calls the
   orchestrator.
2. **Orchestrator** (`orchestrator/`): the single entry point. It validates intent, writes it to
   NetBox, runs Annet, keeps jobs and approvals in Postgres, and runs the drift checks. Intent
   that is a document (fabric settings, routing, overrides, DC links) lives in Postgres, every
   version kept, and is published to NetBox config contexts for the generators.
3. **NetBox** (headless, never shown to operators): the source of truth for devices, interfaces,
   cables, IPAM, ASNs, VRFs and segments. Values that can be derived are not stored; generators
   compute them.
4. **Annet** ([annetutil/annet](https://github.com/annetutil/annet)) runs Diffplane's generators
   (`generators/`, one module per concern) and its rulebook (`rulebook/`: how a VRP patch must be
   ordered and undone), then `gen`, `diff`, `patch` and `deploy`.
5. **gnetcli** is Annet's transport: CLI over SSH. Diffplane builds it with a small patch
   (`deploy/gnetcli/`) so it verifies the host keys pinned in NetBox.

Every deploy is: generate, diff, show the patch, explicit approval, deploy, verify each switch
(empty diff, a fresh collector poll, no fabric link lost), continue. See
[deploy waves](concepts.md#deploys-and-waves).

## Read path: device to GUI

- **Collector** (`collector/`, `python -m collector`): one NETCONF session per active, pinned
  switch, kept open; interface counters every 5 s, interface state and LLDP every 25 s,
  control-plane state (BGP, OSPF, M-LAG, VXLAN tunnels) every 25 s, hardware every 50 s. It
  exports Prometheus metrics; it never configures anything.
- **Prometheus** scrapes the collector and the orchestrator and evaluates the alert rules
  (`deploy/prometheus/alerts.yml`); **Alertmanager** sends them to the receiver set in the GUI.
- On-demand reads (route tables, ARP/MAC tables for path traces, microsegmentation counters) use
  NETCONF `<get>`, or a fixed allow-list of `display` commands where the YANG has no such data.

## Optional services

| Service | What it does |
|---|---|
| `flows` + `clickhouse` | receives sampled NetStream v9 from the switches and keeps aggregates |
| `syslog` | receives switch syslog (warnings and worse) from known switches only |
| `sftpd` | serves software images to switches during an upgrade, one-time accounts |
| `ztp` (profile) | DHCP and TFTP for zero-touch provisioning of registered MACs only |
| `backup` | daily dumps of both databases and NetBox media |

## High availability

`deploy/ha/` runs the same services on two servers and a small witness: Patroni (Postgres),
etcd, keepalived (a virtual IP that follows the Patroni leader), and an agent that starts the
active services only on the leader. See [operations](operations.md#high-availability).

## Code layout

```
orchestrator/     API, job runner, NetBox client, checks, read-path views
  app.py          start-up, middleware, the routers in order, the GUI
  deps.py         shared dependencies: clients, sign-in, roles, fabric limits, INTENT_WRITE
  routers/        the API routes by area (segments, jobs, fabrics, policies, ...)
generators/       Annet generators, one module per concern
rulebook/         custom Annet rules and ordering for VRP
collector/        read-only state collection (NETCONF)
flows/ syslogs/   NetStream and syslog receivers
sftpd/            the image server for software upgrades
gui/              web front end (React + TypeScript, Vite)
seed/             YAML fabric definitions and the script that loads them into NetBox
tests/            unit tests and golden configs (tests/golden/<fabric>/<device>.cfg)
deploy/           Docker Compose, HA, Prometheus, ZTP, environment template
docs/             this documentation
```
