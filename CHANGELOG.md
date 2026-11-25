# Changelog

Each release's images are on ghcr.io (`diffplane-orchestrator`, `diffplane-ztp`), built for
amd64 and arm64 and signed; `deploy/compose.yaml` pulls the release it names.

## v0.2.0 (2026-11-25)

- **Command line:** `diffplane` (`client/diffplane.py`, `pip install ./client`), one file with
  only Python's standard library: fabrics, switches, drift, diffs, and preparing, approving
  (now or in a change window) and following deploys. `diff` and `drift --check` exit 1 when a
  switch differs from intent, for CI. HTTPS only; the password never on the command line.
  See [docs/cli.md](docs/cli.md).
- **Intent in git:** `GET /fabrics/<site>/intent` exports a fabric's intent as YAML;
  `diffplane intent import` shows the changes a file would make and `--apply` makes them through
  the orchestrator's own checks, stopping at the first refusal. Switches still change only
  through review, approval and deploy. A file is untrusted input (safe YAML, no aliases, strict
  types, the fabric it names); microsegmentation, QoS, port protection, port settings, SNMPv3
  readers, DC links and import overrides stay set from the GUI.
- **Typed API:** the main routes (fabrics, switches, segments, services, diff and deploy jobs,
  drift, the signed-in account) declare their answers; the GUI's types for them are generated
  from the schema.
- **Fix:** a fabric rollback to a point with neither a routed segment nor its firewall service
  stopped at deleting the service; the segment now leaves its firewall first.
- README: the demo's first run takes about 18 minutes.

## v0.1.4 (2026-11-24)

- Import report: one card per switch with its patch, what it keeps as found and what is not
  managed; [docs/onboarding.md](docs/onboarding.md): import, a week diff-only, then deploys.
- Compose pulls the signed release images from ghcr.io (`DIFFPLANE_VERSION`); `--build` builds
  them from the checkout. Images run only their own code: the checkout's mounts moved to
  `compose.dev.yaml`, and `scripts/demo.sh` pulls the release images.

## v0.1.3 (2026-11-24)

- Release images are scanned before they are pushed, with an SBOM and build provenance
  attached, and signed with cosign.

## v0.1.2 (2026-11-24)

- Orchestrator image: krb5-libs 1.22.3 and golang.org/x/net v0.62.0 (fixed HIGH CVEs).
- CI: Dependabot waits 7 days before proposing a new version.

## v0.1.1 (2026-11-24)

- Ready-built images for amd64 and arm64 on ghcr.io, built on a version tag.
- Network › Ports shows every switch, spines and border leaves included; services offer a
  centralized fabric's gateway pair; DC links pick the border's M-LAG peer with it.
- A demo GIF in the README.

## v0.1.0 (2026-11-22)

- First public release, with the one-command demo (`scripts/demo.sh`).
