# Command line and scripts

`diffplane` is a small client for the orchestrator's API, for terminals and CI jobs. It is one
Python file with no dependencies beyond the standard library (Python 3.10 or later), and it can
do only what the account it signs in with may do: roles, fabric limits and every deploy rule
stay on the server.

## Install

```sh
pip install ./client            # from a checkout; gives the `diffplane` command
# or run the file itself:
python3 client/diffplane.py --help
```

## Connect

```sh
docker compose exec -T orchestrator cat /tls-public/fabric.crt </dev/null > fabric.crt
export DIFFPLANE_URL=https://127.0.0.1:8000 DIFFPLANE_CA=fabric.crt DIFFPLANE_USER=ci
export DIFFPLANE_PASSWORD=...     # or leave it unset and type it at the prompt
```

The client accepts HTTPS only, verified against `fabric.crt` (the orchestrator's self-signed
certificate) or, without `DIFFPLANE_CA`, the system's trust store. The address must be one of
the certificate's names (`TLS_HOSTNAMES`). The password is never taken from an argument, so it
stays out of shell history and process lists. Each run signs in once and keeps its session in
memory only.

Give scripts their own account (Admin › Users): an **operator** can list, diff and prepare
deploys; approving needs an **approver**, or an operator an admin allowed to approve that
fabric's deploys. Limit the account to the fabrics it works on.

## Commands

| Command | What it does |
|---|---|
| `diffplane fabrics` | the fabrics the account sees |
| `diffplane switches [--fabric F]` | switches, their role, status, address and M-LAG peer |
| `diffplane drift [--fabric F] [--check]` | each switch's latest check against intent; `--check` exits 1 unless all are in sync |
| `diffplane diff --fabric F` or `--switch S ...` | checks the switches now and prints each one's diff; exits 0 if all are as intended, 1 if one differs, 2 if one could not be checked |
| `diffplane deploy prepare --fabric F` | computes the patches for approval and prints them; nothing is sent to a switch |
| `diffplane deploy approve ID [--window-start T --window-end T]` | approves, then follows the rollout; with a window, schedules it (ISO 8601 times with a zone) |
| `diffplane deploy show ID`, `deploy list`, `deploy cancel ID` | a job with its patches and checks, recent jobs, cancel a scheduled job or stop one after its wave |
| `diffplane intent export --fabric F [-o FILE]` | the fabric's intent as YAML |
| `diffplane intent import --fabric F FILE [--apply]` | the changes to intent the file would make, in order (exit 1 if there are any); `--apply` makes them |

`--json` prints the API's answers as they are (the shapes are in `orchestrator/answers.py` and
the OpenAPI schema at `/openapi.json`). Progress lines go to stderr; `-q` silences them. Any
refusal from the API is printed with its reason and exits 2.

## In CI

A job that fails when a fabric's switches differ from intent:

```sh
diffplane -q diff --fabric dc-a
```

A job that prepares a deploy for review (an approver approves it in the GUI, or with
`diffplane deploy approve`):

```sh
diffplane deploy prepare --fabric dc-a
```

The commands wait for their job without a time limit: wrap them in the CI system's own timeout.

## Intent in git

`diffplane intent export` writes what operators set for a fabric: segments with their routing
and port attachments, services, server Eth-Trunks, routing intent, port settings and the
fabric-wide settings (NTP, SNMP, gateway MAC, NetStream, syslog, microsegmentation, QoS,
protection), plus its DC links and what an import kept as found. Devices, cabling and addressing
are not intent and are not in it. Secret values are masked.

`diffplane intent import` plans the changes that bring the fabric's intent to the file's, in the
order a rollback uses, and with `--apply` makes them one by one through the orchestrator's own
checks, stopping at the first refusal. It changes intent only: the switches change through the
usual diff, approval and deploy. A file may change segments, services, server trunks, routing
intent, NTP, the SNMP location and contact, the gateway MAC, NetStream and syslog. The other
parts are set from the GUI by the people allowed to (microsegmentation, QoS, protection, port
settings, SNMPv3 readers, DC links, overrides): the import leaves them as they are and says so.
The file must name the fabric it is imported into.

A flow for a fabric kept in git:

1. Export once and commit the file.
2. A change is a pull request on the file. CI runs `diffplane intent import --fabric dc-a
   dc-a.yaml` and shows its plan in the review (exit 1 means there is something to change).
3. Once merged, a job runs it with `--apply`, then `diffplane deploy prepare --fabric dc-a`;
   an approver reads the patches and approves.
4. Changes made in the GUI meanwhile show up in the next plan as changes the file would undo:
   export again and commit them, or let the file win.
