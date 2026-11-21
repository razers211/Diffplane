# DHCP (dnsmasq) and TFTP (tftpd.py, each address only its own folder) for zero-touch
# provisioning. Its config comes from the orchestrator
# (orchestrator/ztp.py) through the shared /ztp volume; nothing is configured here.
FROM docker.io/library/alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1
RUN apk add --no-cache dnsmasq python3
COPY run.sh tftpd.py /
# Root on purpose: DHCP needs raw sockets and port 67 on the host network.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/bin/sh", "/run.sh"]
