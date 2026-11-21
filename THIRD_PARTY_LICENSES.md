# Third-party licenses

Diffplane is licensed under the Apache License 2.0 (`LICENSE`). It depends on the software below,
each under its own license. Versions are those pinned in `requirements.lock`, `gui/package-lock.json`,
`orchestrator/Dockerfile` and the Compose files. Nothing below is copied into this repository;
it is downloaded when the images are built or pulled.

## Built into the orchestrator image

| Component | Version | License | Notes |
|---|---|---|---|
| [gnetcli](https://github.com/annetutil/gnetcli) server | v1.3.17 | MIT | built from source with `deploy/gnetcli/known-hosts.patch` (host-key verification) |
| [GoFlow2](https://github.com/netsampler/goflow2) | v2.2.3 | BSD-3-Clause | built from source |
| Go module updates in both builds | see `orchestrator/Dockerfile` | BSD-3-Clause (golang.org/x), Apache-2.0 (gRPC), Apache-2.0 / BSD-3-Clause / MIT (klauspost/compress) | |

## Python packages (orchestrator image)

LGPL packages are used unmodified, as installed libraries.

| Package | Version | License |
|---|---|---|
| adaptix | 3.0.0b12 | Apache-2.0 |
| alembic | 1.20.0 | MIT |
| annet | 4.6.14 | MIT |
| annetbox | 1.1.2 | MIT License |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| asyncpg | 0.31.0 | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| bcrypt | 5.0.0 | Apache Software License |
| cattrs | 26.2.1 | MIT License |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| colorlog | 6.12.0 | MIT License |
| contextlog | 1.1 | GNU Lesser General Public License v3 (LGPLv3) |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| dataclass_rest | 0.5.0 | Apache Software License |
| fastapi | 0.162.0 | MIT |
| gnetcli_adapter | 2.8.5 | MIT License |
| gnetclisdk | 1.3.24 | MIT License |
| googleapis-common-protos | 1.76.0 | Apache-2.0 |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| grpcio | 1.85.1 | Apache-2.0 |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.20 | BSD-3-Clause |
| invoke | 3.0.3 | BSD-2-Clause |
| jsonpatch | 1.39 | BSD-3-Clause |
| jsonpointer | 3.7 | BSD License |
| ldap3 | 2.9.1 | GNU Lesser General Public License v3 (LGPLv3) |
| lxml | 6.1.3 | BSD-3-Clause |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| multidict | 7.2.1 | Apache-2.0 |
| ncclient | 0.7.1 | Apache-2.0 |
| netaddr | 1.3.0 | BSD License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| ordered-set | 4.1.0 | MIT License |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| paramiko | 5.1.0 | LGPL-2.1 |
| pip | 25.0.1 | MIT License |
| platformdirs | 4.14.3 | MIT |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |
| pyasn1 | 0.6.5 | BSD-2-Clause |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.2 | MIT |
| pydantic-settings | 2.16.2 | MIT |
| pydantic_core | 2.50.2 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyNaCl | 1.6.2 | Apache Software License |
| pyrad | 2.4 | BSD License |
| python-dateutil | 2.9.0.post0 | BSD License; Apache Software License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT License |
| requests | 2.34.2 | Apache Software License |
| requests-cache | 1.3.3 | BSD-2-Clause |
| ruamel.yaml | 0.19.1 | MIT License |
| six | 1.17.0 | MIT License |
| SQLAlchemy | 2.1.3 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| tabulate | 0.10.0 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| url-normalize | 3.1.0 | MIT |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| valkit | 0.1.4 | GNU Lesser General Public License v3 (LGPLv3) |
| yarl | 1.25.1 | Apache-2.0 |

## GUI (bundled into the built page)

| Package | Version | License |
|---|---|---|
| @fontsource/overpass | 5.3.0 | OFL-1.1 |
| @fontsource/overpass-mono | 5.3.0 | OFL-1.1 |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| scheduler | 0.28.0 | MIT |

Development-only tools (TypeScript, Vite, Vitest and their dependencies) are not shipped.

## Container images run alongside (separate programs, not modified)

| Image | Licence of the software |
|---|---|
| NetBox (`netboxcommunity/netbox`) | Apache-2.0 |
| PostgreSQL (`postgres`) | PostgreSQL License |
| Valkey (`valkey/valkey`) | BSD-3-Clause |
| ClickHouse server and Keeper | Apache-2.0 |
| Prometheus, Alertmanager | Apache-2.0 |
| etcd (HA only) | Apache-2.0 |
| Patroni (HA only, built on `postgres:17-alpine`) | MIT |
| keepalived (HA only, Alpine package) | GPL-2.0-or-later |
| dnsmasq (ZTP profile only, Alpine package) | GPL-2.0-only or GPL-3.0-only |
| Docker CLI (HA agent) | Apache-2.0 |
| OpenLDAP and FreeRADIUS (test profile `directory-test` only) | OpenLDAP Public License; GPL-2.0 |

Fonts: Overpass and Overpass Mono, SIL Open Font License 1.1.
