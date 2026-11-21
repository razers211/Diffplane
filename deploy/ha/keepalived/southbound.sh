#!/bin/sh
# the platform's traffic to the switches leaves from the southbound VIP.
# keepalived runs this on every state change: `up <vip> <interface>` on MASTER adds one SNAT rule for
# everything leaving through the second NIC; `down ...` (BACKUP, FAULT, stop) removes it. Host network
# and NET_ADMIN, so the rule is the host's; Docker's own masquerade would use the server's address.
set -u
action=$1 vip=$2 dev=$3
rule="POSTROUTING -o $dev -j SNAT --to-source $vip"
while iptables -t nat -C $rule 2>/dev/null; do iptables -t nat -D $rule; done
if [ "$action" = up ]; then
    iptables -t nat -I $rule
    echo "southbound: traffic out of $dev now leaves from $vip"
else
    echo "southbound: SNAT to $vip removed"
fi
