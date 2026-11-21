#!/bin/sh
# HA agent: the active services run only where the local Patroni is the leader. Every
# $HA_INTERVAL seconds it asks Patroni (http://127.0.0.1:8008/primary answers 200 on the leader only);
# after $HA_CONFIRM identical answers in a row it starts or stops them, and while leader it makes sure
# they still run every $HA_RECHECK rounds. Runs in the ha-agent container (deploy/ha/server.yaml).
set -u
ACTIVE="redis netbox netbox-worker orchestrator collector flows syslog sftpd ztp"
CONFIRM=${HA_CONFIRM:-2}
INTERVAL=${HA_INTERVAL:-3}
RECHECK=${HA_RECHECK:-10}

compose() {
    docker compose --env-file .env --env-file ha/out/ha.env -f compose.yaml -f ha/server.yaml --profile active "$@"
}

role() {
    if wget -q -T 2 -O /dev/null "${PATRONI_URL:-http://127.0.0.1:8008}/primary"; then echo leader; else echo replica; fi
}

applied=""      # what was last done: leader (started) or replica (stopped)
seen=""
count=0
rounds=0
while :; do
    now=$(role)
    if [ "$now" = "$seen" ]; then count=$((count + 1)); else seen=$now; count=1; fi
    rounds=$((rounds + 1))
    if [ "$count" -ge "$CONFIRM" ]; then
        if [ "$now" != "$applied" ] || { [ "$now" = leader ] && [ $((rounds % RECHECK)) -eq 0 ]; }; then
            if [ "$now" = leader ]; then
                [ "$now" != "$applied" ] && echo "ha-agent: this server is the Patroni leader: starting $ACTIVE"
                # with their dependencies, so NetBox is healthy before the orchestrator starts
                compose up -d --no-build --pull never $ACTIVE && applied=leader
            else
                echo "ha-agent: this server is not the Patroni leader: stopping $ACTIVE"
                compose stop $ACTIVE && applied=replica
            fi
        fi
        if [ -n "${HA_ONCE:-}" ]; then echo "applied=$applied"; exit 0; fi   # tests: one decision, then stop
    fi
    sleep "$INTERVAL"
done
