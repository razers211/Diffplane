#!/bin/sh
# Backups of the platform's state: the NetBox database (all
# intent), the platform database (users, jobs, approvals, audit) and NetBox media.
# One folder per run in $BACKUP_DIR (deploy/backups/ on the host), kept $BACKUP_KEEP_DAYS days.
#
#   backup.sh          run forever: back up whenever the newest backup is older than
#                      $BACKUP_INTERVAL_SECONDS (so a restart does not skip a day)
#   backup.sh once     one backup now, then prune
#
# Restore: see docs/operations.md. The dumps hold secrets (NetBox 4.4 stores classic API
# tokens unhashed; password hashes; session tokens): keep the folder private.
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
INTERVAL=${BACKUP_INTERVAL_SECONDS:-86400}
MEDIA_DIR=${NETBOX_MEDIA_DIR:-/netbox-media}

dump() {  # host port user password database file
    PGPASSWORD=$4 pg_dump --host "$1" --port "$2" --username "$3" --dbname "$5" \
        --format custom --no-owner --file "$6"
}

backup() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    work="$BACKUP_DIR/.$stamp.partial"
    mkdir -p "$work"
    dump "${NETBOX_DB_HOST:-netbox-postgres}" "${NETBOX_DB_PORT:-5432}" "${NETBOX_DB_USER:-netbox}" \
        "${NETBOX_DB_PASSWORD:-}" "${NETBOX_DB_NAME:-netbox}" "$work/netbox.dump"
    dump "${PLATFORM_DB_HOST:-postgres}" "${PLATFORM_DB_PORT:-5432}" "${PLATFORM_DB_USER:-platform}" \
        "${PLATFORM_DB_PASSWORD:-}" "${PLATFORM_DB_NAME:-platform}" "$work/platform.dump"
    tar -C "$MEDIA_DIR" -czf "$work/netbox-media.tar.gz" .
    mv "$work" "$BACKUP_DIR/$stamp"   # complete backups only ever appear whole
    echo "backup $BACKUP_DIR/$stamp done"
}

prune() {
    find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*Z' -mtime +"$KEEP_DAYS" \
        -exec rm -rf {} + -exec echo "pruned {}" \;
    find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '.*.partial' -mmin +60 -exec rm -rf {} +
}

newest_age() {  # seconds since the newest complete backup, or a large number
    newest=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*Z' | sort | tail -n 1)
    if [ -z "$newest" ]; then echo 999999999; return; fi
    echo $(( $(date +%s) - $(stat -c %Y "$newest") ))
}

mkdir -p "$BACKUP_DIR"
if [ "${1:-}" = "once" ]; then
    backup
    prune
    exit 0
fi
while true; do
    if [ "$(newest_age)" -ge "$INTERVAL" ]; then
        backup || echo "backup failed; retrying in an hour" >&2
        prune
    fi
    sleep 3600
done
