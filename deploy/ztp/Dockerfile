# DHCP (dnsmasq) and TFTP (tftpd.py, each address only its own folder) for zero-touch
# provisioning. Its config comes from the orchestrator
# (orchestrator/ztp.py) through the shared /ztp volume; nothing is configured here.
FROM docker.io/library/alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN apk add --no-cache dnsmasq python3
COPY run.sh tftpd.py /
# Root on purpose: DHCP needs raw sockets and port 67 on the host network.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/bin/sh", "/run.sh"]
