# keepalived for the VIPs. Its check asks the local Patroni whether it is the leader (curl: busybox
# wget made the check exit 127, command not found, on the VMs); iptables for the southbound SNAT rule.
FROM docker.io/library/alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
RUN apk add --no-cache keepalived curl iptables
COPY southbound.sh /usr/local/bin/southbound.sh
# Root on purpose: VRRP and the southbound SNAT rule need it (only NET_ADMIN, NET_RAW, NET_BROADCAST in Compose).
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["keepalived", "--dont-fork", "--log-console", "--log-detail", "-f", "/etc/keepalived/keepalived.conf"]
