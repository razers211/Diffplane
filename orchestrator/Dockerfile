# Build context is the repository root (see deploy/compose.yaml).

# gnetcli server: Annet's transport to the switches (fetch and deploy).
# Keep the version in step with the gnetclisdk Python package that gnetcli_adapter pulls in.
# Patched (deploy/gnetcli/, proposed upstream): with KNOWN_HOSTS_FILE set it verifies SSH host
# keys; unpatched it accepts any key. The build fails if the patch no longer applies or its
# tests fail, so an upgrade cannot drop it silently.
# Both Go tools are built with a supported Go, and their dependencies with known CVEs are raised
# to fixed versions (`go get`; x/net brings matching x/crypto and x/text). The upstream releases
# stay pinned, and their whole test suites run. Raise them again when `trivy image` reports more.
FROM docker.io/golang:1.27-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS gnetcli
ARG GNETCLI_VERSION=v1.3.17
COPY deploy/gnetcli/known-hosts.patch /tmp/known-hosts.patch
RUN git clone --quiet --depth 1 --branch ${GNETCLI_VERSION} https://github.com/annetutil/gnetcli /src/gnetcli \
 && cd /src/gnetcli \
 && git apply /tmp/known-hosts.patch \
 && go get google.golang.org/grpc@v1.83.2 golang.org/x/net@v0.60.0 \
 && go mod tidy \
 && go test ./... \
 && CGO_ENABLED=0 go build -o /out/gnetcli_server ./cmd/gnetcli_server

# GoFlow2: the NetFlow v9 decoder the flows service runs (python -m flows). Pinned.
FROM docker.io/golang:1.27-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS goflow2
ARG GOFLOW2_VERSION=v2.2.3
RUN git clone --quiet --depth 1 --branch ${GOFLOW2_VERSION} https://github.com/netsampler/goflow2 /src/goflow2 \
 && cd /src/goflow2 \
 && go get golang.org/x/net@v0.60.0 github.com/klauspost/compress@v1.18.7 \
 && go mod tidy \
 && go test ./... \
 && CGO_ENABLED=0 go build -o /out/goflow2 ./cmd/goflow2

# GUI: built once here, served by the orchestrator at /ui/.
FROM docker.io/node:24-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS gui
WORKDIR /gui
COPY gui/package.json gui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY gui ./
RUN npm test && npm run build

# Alpine: no util-linux, systemd, perl or the rest of Debian's base system, whose CVEs Debian had
# not fixed. Every locked dependency installs from a musl wheel; the Go binaries are static.
FROM docker.io/python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

COPY --from=gnetcli /out/gnetcli_server /usr/local/bin/gnetcli_server
COPY --from=goflow2 /out/goflow2 /usr/local/bin/goflow2

WORKDIR /app
# Dependencies first, each checked against its hash in the lockfile.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml ./
COPY orchestrator ./orchestrator
COPY seed ./seed
COPY generators ./generators
COPY rulebook ./rulebook
COPY collector ./collector
COPY flows ./flows
COPY syslogs ./syslogs
COPY sftpd ./sftpd
COPY deploy/annet/context.yml ./deploy/annet/context.yml
COPY --from=gui /gui/dist ./gui/dist
RUN pip install --no-cache-dir --no-deps .

# Annet reads its context from here; NetBox and device credentials come from the environment.
# PYTHONPATH: /app (where compose mounts generators/, rulebook/, seed/) wins over the copy
# `pip install .` put in site-packages, for every process: the orchestrator's
# `python -m annet`, a manual `annet diff`, the seed. Otherwise they ran different code.
ENV ANN_CONTEXT_CONFIG_PATH=/app/deploy/annet/context.yml \
    PYTHONPATH=/app \
    HOME=/tmp
# The self-signed TLS certificate and key (orchestrator/tls.py); Compose mounts volumes here,
# which take this ownership when first created.
RUN mkdir -p /tls /tls-public && chown nobody /tls /tls-public && chmod 700 /tls
# ZTP files for the dnsmasq container: written here, read by dnsmasq's TFTP user.
RUN mkdir -p /ztp && chown nobody /ztp && chmod 755 /ztp
# GUI settings shared with the collector: the collector's login, readable by nobody else.
RUN mkdir -p /shared && chown nobody /shared && chmod 700 /shared
# Alertmanager's configuration: written here, read by Alertmanager (also nobody).
RUN mkdir -p /alertmanager-config && chown nobody /alertmanager-config && chmod 700 /alertmanager-config
# Uploaded switch software: written by the orchestrator, read by sftpd.
RUN mkdir -p /images /progress && chown nobody /images /progress && chmod 755 /images /progress
USER nobody
EXPOSE 8000
# HTTPS only: makes or reuses the self-signed certificate, then runs uvicorn with it.
CMD ["python", "-m", "orchestrator.serve"]
