# Quick start

Just looking? `scripts/demo.sh` from the repository root does all of this in one command
(secrets, start, two example fabrics, an admin account) and prints how to sign in.

This brings up Diffplane on one machine with the synthetic example fabric. Nothing in it
talks to a switch: every device of the example fabric is *planned* (modelled only).

You need Docker with Compose v2 and Python 3 on the host.

## 1. Secrets

```sh
cd deploy
python3 gen_env.py          # writes deploy/.env with random secrets (gitignored, mode 0600)
```

Read `deploy/.env.example` for what each value does. Switch credentials (`GNETCLI_DEV_*`,
`COLLECTOR_*`) can stay empty until you have a lab switch; they can also be set later from the
GUI (Admin › Settings).

The example fabrics have M-LAG pairs, whose generated config carries a DFS-group key: set
`MLAG_DFS_PASSWORD` in `deploy/.env` (any value for a trial, e.g. `openssl rand -hex 16`) or later
in Admin › Settings, or generating a leaf's config fails with "MLAG_DFS_PASSWORD is not set". On
real switches it must be the key the pairs use. Until a switch login is set, the collector stops
after a few tries; that is expected.

## 2. Start

```sh
docker compose up -d
docker compose ps                               # wait until everything is healthy
```

This pulls the release images named in `deploy/compose.yaml` from ghcr.io. They match a checkout of
that release tag; on a later commit, or to run code you changed, use `docker compose up --build -d`
to build them here instead (takes a while). To check an image's signature first:

```sh
cosign verify ghcr.io/razers211/diffplane-orchestrator:v0.1.4 \
  --certificate-identity-regexp '^https://github.com/razers211/Diffplane/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

NetBox migrates its database on first start, which takes a minute or two; the orchestrator waits
for it.

The orchestrator serves **HTTPS only**, with a self-signed certificate it makes on first start.
Save it once so `curl` can trust exactly it:

```sh
docker compose exec -T orchestrator cat /tls-public/fabric.crt </dev/null > fabric.crt
curl --cacert fabric.crt https://127.0.0.1:8000/health
```

`/health` answers 200 with the NetBox version once the orchestrator reaches NetBox.

## 3. First admin

Open <https://127.0.0.1:8000/>. Compare the certificate fingerprint your browser shows with the
one in the log (`docker compose logs orchestrator | grep TLS`) before accepting it.

The first page asks for a one-time setup code:

```sh
docker compose logs orchestrator | grep SETUP
```

Enter it with the first admin's name and password. Skip the switch credentials and the first
fabric for now.

## 4. The example fabric

```sh
docker compose exec orchestrator python -m seed seed/fabrics/example.yaml
```

It prints every change it makes; running it again prints `No changes.` Pick the fabric
`example` in the GUI: the Overview shows the topology (two spines, two M-LAG leaf pairs), and
each switch's **Config and diff** tab shows the configuration Diffplane generates for it.

From the command line:

```sh
docker compose exec orchestrator python -m annet gen --no-label leaf1
```

Other example fabrics in `seed/fabrics/` show a centralized fabric (`example-central.yaml`,
`example-central-spines.yaml`) and the PIP layout (`example-pip.yaml`).

## 5. Try intent

In **Network › Segments**, create a segment (name, VNI, bridge-domain ID) and attach it to a
leaf port. The yellow bar appears: **Changes › Pending** shows what each switch's config would
become. Because the example switches are planned, nothing can be deployed: that needs an active
switch with pinned host keys, in a fabric where an admin turned deploys on.

## Next

- [Concepts](concepts.md): fabrics, segments, services, routing intent, import, deploy waves.
- [Architecture](architecture.md): how the pieces fit together.
- [Operations](operations.md): users, drift, alerts, backups, bringing up a real fabric.
- [Security model](security-model.md): read it before connecting a real switch.

## Stop

```sh
docker compose down          # keeps the volumes (your data)
docker compose down -v       # removes them too
```
