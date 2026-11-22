#!/bin/sh
# One-command demo: start Diffplane on this machine with two example fabrics and an admin account.
# Nothing talks to a switch: every example device is planned (modelled only), deploys stay off.
#
#   scripts/demo.sh            start (again) and print how to sign in
#   scripts/demo.sh down       stop, keeping the data
#   scripts/demo.sh reset      stop and delete the demo's data
#
# Needs Docker with Compose v2 and Python 3. Uses deploy/.env, made here if missing.
set -eu
cd "$(dirname "$0")/../deploy"
dc() { docker compose "$@"; }

case "${1:-up}" in
  down) dc down; exit ;;
  reset) dc down -v; exit ;;
  up) ;;
  *) echo "usage: $0 [up|down|reset]" >&2; exit 2 ;;
esac

if [ ! -f .env ]; then
  python3 gen_env.py
  # Leaves' generated config carries a DFS-group key; any value will do for the demo.
  sed -i.bak "s/^MLAG_DFS_PASSWORD=$/MLAG_DFS_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(16))')/" .env
  rm -f .env.bak
elif grep -q '^MLAG_DFS_PASSWORD=$' .env; then
  echo "note: MLAG_DFS_PASSWORD is empty in deploy/.env: leaves' configs will not generate until it is set" >&2
fi

echo "Building and starting (the first run takes several minutes: images, then NetBox's migrations)..."
dc up --build -d

printf 'Waiting for the orchestrator'
i=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q orchestrator)" 2>/dev/null)" = healthy ]; do
  i=$((i + 1))
  if [ "$i" -gt 120 ]; then echo; echo "not healthy after 20 minutes: docker compose -f deploy/compose.yaml logs orchestrator" >&2; exit 1; fi
  printf .; sleep 10
done
echo

# NetBox is slow for a while after its first start: retry instead of failing the demo.
for fabric in example example-central; do
  tries=0
  until dc exec -T orchestrator python -m seed "seed/fabrics/$fabric.yaml" >/dev/null; do
    tries=$((tries + 1))
    if [ "$tries" -ge 10 ]; then echo "could not load $fabric" >&2; exit 1; fi
    echo "NetBox is still starting; retrying in 20 s"; sleep 20
  done
done

# The account is made once; a second run leaves it (and its password) alone.
password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')
if echo "$password" | dc exec -T orchestrator python -c '
import asyncio, sys
from orchestrator.users import run
try:
    asyncio.run(run("create", "demo", sys.stdin.readline().strip(), "admin"))
except ValueError:
    sys.exit(3)' 2>/dev/null; then
  login="demo / $password   (shown once; change it in the GUI under your name)"
else
  login="demo, with the password printed by the first run (reset it: docker compose exec orchestrator python -m orchestrator.users set-password demo)"
fi

port=$(grep -E '^ORCHESTRATOR_PORT=' .env | cut -d= -f2)
fingerprint=$(dc logs orchestrator 2>/dev/null | sed -n 's/.*SHA256 fingerprint \([0-9A-F:]*\).*/\1/p' | tail -1)
cat <<EOF

Diffplane is running.

  Open     https://127.0.0.1:${port:-8000}/
  Sign in  $login
  Cert     self-signed, SHA-256 ${fingerprint:-in: docker compose -f deploy/compose.yaml logs orchestrator | grep TLS}

Fabrics: "example" (symmetric: two spines, two M-LAG leaf pairs) and "example-central"
(centralized: the gateways on one pair). Try Network › Segments: create a segment, attach it to a
port, then Changes › Pending shows the patch every switch would get.

Stop: scripts/demo.sh down    Delete the demo's data: scripts/demo.sh reset
EOF
