# DHCP (dnsmasq) and TFTP (tftpd.py, each address only its own folder) for zero-touch
# provisioning. Its config comes from the orchestrator
# (orchestrator/ztp.py) through the shared /ztp volume; nothing is configured here.
FROM docker.io/library/alpine:3.22.8@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f
RUN apk add --no-cache dnsmasq python3
COPY run.sh tftpd.py /
# Root on purpose: DHCP needs raw sockets and port 67 on the host network.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/bin/sh", "/run.sh"]
