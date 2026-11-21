# Diffplane

Diffplane manages a leaf-spine data-centre fabric (BGP EVPN, VXLAN, M-LAG) from one web GUI.
Operators describe what they want (segments, ports, services, routing); Diffplane generates
each switch's configuration, shows the exact patch, and deploys it only after a person approves
it, switch by switch, verifying each one.

It is compatible with Huawei CloudEngine data-centre switches running VRP V300 (see
[supported switches](docs/supported-switches.md)). Diffplane is an independent project, not
affiliated with or endorsed by Huawei.

> **Use at your own risk.** Diffplane changes production network configuration. Test it in a lab
> first, bring real fabrics up diff-only, and read the [security model](docs/security-model.md).

## How it works

```
GUI ─► orchestrator (FastAPI) ─► NetBox (source of truth)
                │
                └─► Annet generators ─► diff / patch ─► approval ─► gnetcli (SSH) ─► switches
switches ─► collector (NETCONF, read-only) ─► Prometheus ─► topology, health, drift
```

- **Never deploy without a diff**: generate, diff, show the patch, approve, deploy in pair-safe
  waves, verify each switch, continue.
- **Store intent, derive the rest**: addresses, route targets, VNIs and numbering are computed.
- **Pinned host keys**, deploys off by default, and the management plane is never touched.

More: [architecture](docs/architecture.md), [concepts](docs/concepts.md).

## Quick start

```sh
cd deploy
python3 gen_env.py                     # deploy/.env with random secrets
docker compose up --build -d
docker compose logs orchestrator | grep SETUP     # one-time code for the first admin
```

Open <https://127.0.0.1:8000/>, create the first admin, then load the synthetic example fabric:

```sh
docker compose exec orchestrator python -m seed seed/fabrics/example.yaml
```

The full walk-through is in [docs/quickstart.md](docs/quickstart.md).

## Documentation

| | |
|---|---|
| [Quick start](docs/quickstart.md) | run it locally with the example fabric |
| [Architecture](docs/architecture.md) | the components and how they talk |
| [Concepts](docs/concepts.md) | fabrics, segments, services, routing intent, import, deploy waves |
| [Supported switches](docs/supported-switches.md) | models, software, what is verified |
| [Security model](docs/security-model.md) | what protects the switches, secrets, known risks |
| [Operations](docs/operations.md) | users, drift, alerts, backups, onboarding, HA, upgrades |
| [Roadmap](ROADMAP.md) | what exists and what needs verification |

## Security

Diffplane can change every switch it manages, so it is built to refuse rather than guess:

- **Nothing reaches a switch without an approved patch.** Each switch's patch is recomputed just
  before it is sent and must equal the approved one.
- **Deploys are off until an admin allows them** per fabric; new and imported fabrics start
  diff-only.
- **Switches are trusted only by pinned host keys**, compared by a person with the console.
- **The management interface, its VPN instance and the platform's own accounts are never
  managed**, so a bad deploy cannot lock the platform out.
- **People:** server-side sessions, roles from viewer to admin, an audit trail, scrypt
  password hashes, a sign-in throttle, same-origin checks and a strict Content-Security-Policy.
  HTTPS only.
- **Secrets** come from `deploy/.env` (generated, never committed) or write-only settings, and
  are masked in every API answer.

Read the [security model](docs/security-model.md) (with its known risks) before running it near
a real network, and the [security review](SECURITY_REVIEW.md). Report vulnerabilities privately as
described in [SECURITY.md](SECURITY.md).

## Development

```sh
pip install -e '.[test]'
pytest                                  # unit, golden-config and patch tests

cd gui
npm ci
npm run dev        # http://127.0.0.1:5173/ui/, API calls proxied to 127.0.0.1:8000
npx vitest run
```

Golden configs live in `tests/golden/<fabric>/<device>.cfg`; regenerate them with
`UPDATE_GOLDEN=1 pytest tests/generators` and review the diff. Contributor notes are in
[CONTRIBUTING.md](CONTRIBUTING.md).

Without installing anything on the host, run the tests in a throwaway container from the
orchestrator image (from the repository root, after `docker compose build orchestrator` in
`deploy/`):

```sh
scripts/test.sh
```

`pip install -e '.[test]'` on a glibc host uses `pgserver` instead (an embedded PostgreSQL).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md).

## License and trademarks

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE); third-party software is listed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Huawei, CloudEngine and VRP are trademarks of
Huawei Technologies Co., Ltd., used here only to state compatibility.
