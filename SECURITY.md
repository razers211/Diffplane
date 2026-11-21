# Security policy

Diffplane changes the configuration of data-centre switches, so security reports are welcome
and handled first.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting instead: on the repository page,
**Security › Report a vulnerability**. Include what you found, how to reproduce it, the version
or commit, and what an attacker could do with it.

You should get an answer within a week. Fixes are released as soon as they are ready, and the
report is credited unless you prefer otherwise.

## Supported versions

Only the latest commit of `main` gets security fixes.

## Scope

In scope: the orchestrator, collector, receivers (flows, syslog, SFTP, ZTP), the GUI, the
generators and rulebook, and the Compose and HA files. How the platform is meant to be deployed
and what it protects is described in [docs/security-model.md](docs/security-model.md); the last
review is in [SECURITY_REVIEW.md](SECURITY_REVIEW.md).

Out of scope: vulnerabilities in NetBox, Annet, gnetcli, the switches' own software or other
third-party components (report them upstream), and findings that need an attacker who already
has admin access to the platform or to its server.
