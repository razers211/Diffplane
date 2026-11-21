# Security review

A review of this repository before its first public release. It covers the code, the
container setup and the dependencies as published. How the platform protects switches,
secrets and accounts is described in [docs/security-model.md](docs/security-model.md); how
to report a vulnerability is in [SECURITY.md](SECURITY.md).

## Method

- **Automated scans, each run in a throwaway container:** gitleaks 8.21.2 and trufflehog
  3.88.0 (secrets), bandit 1.8.0 and semgrep 1.99.0 (`p/default`, `p/python`, `p/dockerfile`),
  pip-audit 2.7.3 (the orchestrator image's 74 Python packages), npm audit (the GUI), trivy
  0.58.1 (filesystem, misconfiguration and the built image), hadolint 2.12.0 (Dockerfiles).
- **Manual review, area by area:**
  - authentication and sessions;
  - authorisation;
  - every place user input reaches a switch command, a database or LDAP query, a file path or
    another host;
  - device safety, ZTP and network exposure;
  - containers, the supply chain, logging and the audit trail.

## Results of the scans

| Tool | Result |
|---|---|
| gitleaks, trufflehog | no secrets |
| npm audit | 0 vulnerabilities |
| pip-audit | 0 known vulnerabilities |
| bandit | no actionable finding; see "Reviewed and accepted" |
| semgrep | 6 findings: 1 fixed, 5 reviewed and accepted |
| trivy (filesystem) | no vulnerable dependency, no secret; Dockerfile notes below |
| trivy (image) | none HIGH or CRITICAL after the fixes below (Go binaries rebuilt, Alpine base) |
| hadolint | style warnings only: unpinned `apk add`, `cd` in `RUN` |

## Fixed

- **Deploys are off by default.**
  - `DEPLOY_ALLOWED_SITES` is now empty unless set, in the code, in Compose and in
    `deploy/.env.example`.
  - No fabric receives a deploy until an admin allows it.
- **Sessions and sign-in.**
  - **Secure cookie by default:** the session cookie is `Secure` unless `COOKIE_SECURE=false`.
    Before, a missing variable meant not Secure.
  - **Sign-in throttle:** after 10 failed sign-ins from one address in 15 minutes, that address
    gets `429` with `Retry-After`.
    - It is per address, never per account, so nobody can lock someone else out.
    - Failures are also audited, as before.
  - **No user enumeration by timing:** every refused sign-in costs one password hash, whether
    or not the account exists, is disabled, or no directory is set up.
  - The user count is computed in SQL.
- **CSRF.**
  - A `POST`, `PUT`, `PATCH` or `DELETE` whose `Origin` names another site is refused before
    it runs.
  - This sits on top of the `SameSite=Strict` cookie and JSON-only bodies.
- **Security headers on every response:**
  - `Content-Security-Policy`: scripts and connections from the platform itself only, and
    `frame-ancestors 'none'`;
  - `Strict-Transport-Security`;
  - `X-Frame-Options: DENY`;
  - `X-Content-Type-Options: nosniff`;
  - `Referrer-Policy: no-referrer`;
  - `Cross-Origin-Opener-Policy`.

  The built GUI has no inline script, so the policy needs no exception for scripts.
- **TLS:** on TLS 1.2, only forward-secret AEAD cipher suites (ECDHE with AES-GCM or
  ChaCha20). TLS 1.3 is unchanged.
- **Input reaching switch configuration.**
  - **Name checks:** six checks ended in `$`, which in Python also accepts a trailing newline.
    They now use `\Z`, so a name cannot carry a line break into a generated configuration.
    The checks are fabric and switch names, location names, ZTP logins, serial numbers, and
    VPN instance names on the route view.
  - **SNMP location and contact:** these now accept printable ASCII only, without `"` or `?`,
    like port descriptions. Before, only line breaks and quotes were refused.
- **Containers.**
  - The orchestrator, collector, flows, syslog and SFTP services now run with
    all capabilities dropped and a read-only root filesystem (writes go to
    `/tmp` and their volumes). They already ran as `nobody`.
  - Every image in Compose, the HA files and the Dockerfiles is pinned by digest as well as by
    tag. A test fails on any unpinned image.
- **Supply chain.**
  - Python dependencies are locked with hashes in `requirements.lock`: the 73 exact versions
    the tested image ran.
  - The image installs them with `--require-hashes`, then the project with `--no-deps`.
  - A test checks that every `pyproject.toml` dependency is in the lock.
  - The GUI already had `package-lock.json` with integrity hashes and exact pins.
  - The gnetcli patch is documented in `deploy/gnetcli/UPSTREAM.md`; the build fails if it
    stops applying.
  - An SBOM in CycloneDX form is generated with trivy (CI, Phase 4).
- **Repository hygiene.**
  - `.gitignore` now also covers `deploy/.env.*` (except the example), private keys and
    certificates, and local tooling folders.
  - Stale links to the old runbook now point to `docs/operations.md`.
- **Tests added.**
  - `tests/orchestrator/test_route_access.py` walks every API route:
    - without a session, every route but the public ones answers 401;
    - every role below a route's declared role gets 403 (over 100 route and role pairs).
  - `tests/orchestrator/test_web_security.py` covers the headers, the cross-site refusal and
    the throttle and the metrics token.

- **Decided by the maintainer at the review checkpoint.**
  - **`/metrics` needs a bearer token.** It exposed switch names, drift state, ports in use and
    traffic anomalies to anyone reaching the port. It now answers only to
    `Authorization: Bearer <METRICS_TOKEN>` (constant-time comparison); `gen_env.py` generates
    the token and Compose hands it to Prometheus as a secret file.
  - **Go binaries rebuilt.** Both are built with Go 1.27; gnetcli with grpc 1.83.2 and x/net
    0.60.0, GoFlow2 with x/net 0.60.0 and klauspost/compress 1.18.7 (x/crypto and x/text
    follow). Each module's own tests run in the build.
  - **ZTP provisioning window.** A registration's files are served for `ZTP_WINDOW_HOURS`
    (24 by default) and then withdrawn from DHCP and TFTP; the device page says so and offers
    "Register again". The first-boot file still carries the platform's passwords (see
    `docs/security-model.md`).
  - **Stronger password hashing.** scrypt N=2^17, r=8, p=1 (128 MiB), at most two hashes at a
    time; older hashes still sign in and are upgraded at that sign-in.
  - **`gen_env.py`** no longer fills `SFTP_ADDRESS`, `ZTP_WINDOW_HOURS` or
    `DEPLOY_ALLOWED_SITES` with random values (`SFTP_ADDRESS` was a bug before this review).
- **Alpine runtime image.** The Debian base carried 9 HIGH CVEs (45 package findings: util-linux,
  ncurses, systemd libraries, acl, sqlite, perl) that Debian had not fixed. The orchestrator image
  now runs on `python:3.12-alpine`: every locked dependency installs from a musl wheel with its
  hash, the Go binaries were already static, and trivy finds no HIGH or CRITICAL CVE. The database
  tests use Alpine's PostgreSQL there (`tests/pg.py`; `pgserver` on glibc hosts) and fail rather
  than skip in CI.
- **Kept enforced by CI** (`.github/workflows/ci.yml`, `codeql.yml`, `dependabot.yml`):
  - gitleaks over the whole history, bandit (high severity and confidence), semgrep, pip-audit
    on the hash-locked dependencies, npm audit, trivy on the repository and on the built image
    (fixable HIGH and CRITICAL fail), CodeQL, and a CycloneDX SBOM of the image;
  - the accepted bandit and semgrep findings below are marked in place (`# nosec`,
    `# nosemgrep`), each with its reason on the line above;
  - every action is pinned to a commit SHA, every scanner image to a digest;
  - Dependabot proposes updates for Python, npm, actions, Dockerfiles and Compose images.

## Reviewed and accepted

- **Public routes:**
  - `/health`;
  - `/auth/login` and `/auth/logout`;
  - `/setup`: only while no account exists, guarded by a one-time code printed in the
    orchestrator's log, replaced after 10 wrong tries;
- **Passwords.**
  - Local passwords are hashed with scrypt (salted, constant-time comparison; see Fixed for the
    strength).
  - Sessions are random 256-bit tokens, stored hashed, with an expiry (12 hours by default) and
    a logout that deletes them.
- **Read-only switch commands.**
  - The route view, the import and upgrade steps send only commands built from checked values:
    - `routes.DISPLAY`, matched against the whole command;
    - the fixed `display` list in `ssh_read.py`;
    - file names in `switch_cli.py`, checked by `images.NAME`, or taken from the switch's own
      listing, which cannot contain spaces.
  - Commands are passed as process arguments, never through a shell.
- **Queries.**
  - ClickHouse queries take every value as a bound parameter (bandit B608 are false positives).
  - LDAP filters escape the user name (`escape_filter_chars`).
  - NetBox filters are sent as encoded query parameters.
- **XML.**
  - Switch replies are parsed by Python's standard library (expat: no external entities,
    entity expansion limited).
  - `lxml.etree.fromstring` is only used on the platform's own requests (bandit B314/B320).
- **Host keys.**
  - Pinned per device and checked by gnetcli (our patch, `deploy/gnetcli/`), the collector and
    the import.
  - The collector compares the MD5 fingerprint that ncclient's callback provides (bandit B324).
    Defeating that needs a second preimage of a pinned key, not a collision.
- **Containers that need root.**
  - Keepalived (VRRP, the southbound SNAT rule) and the ZTP DHCP/TFTP server (raw sockets on
    the host network) run as root with only the capabilities they need. Both are opt-in: the HA
    files, and the Compose profile `ztp`.
- **Published ports.**
  - Only the orchestrator's HTTPS port and the receivers the switches send to: NetFlow,
    syslog, SFTP.
  - All bind to `127.0.0.1` unless `.env` says otherwise.
  - NetBox, the databases, Redis, ClickHouse, Prometheus and Alertmanager publish nothing.
- **Logging and audit.**
  - The audit trail has no API to change or delete entries.
  - Values of every secret a generator writes are masked in API responses
    (`orchestrator/redact.py`).
  - No secret is accepted as a query parameter, so none reaches the request log.
  - gnetcli does not log the one-time SFTP password.

## Open

Nothing from this review. Known, unfixed CVEs in third-party components are caught by CI's image
scan as soon as a fix exists.
