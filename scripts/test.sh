#!/bin/sh
# Run the Python tests in a throwaway container from the orchestrator image: nothing to install on
# the host. Extra arguments go to pytest, e.g.  scripts/test.sh tests/generators -k bgp
#
# The container starts as root only to add PostgreSQL (for the database tests) and pytest; the
# tests themselves run as nobody. IMAGE picks another image tag (CI uses its own build).
set -eu

cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-diffplane-orchestrator:local}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image $IMAGE not found: build it first with  (cd deploy && docker compose build orchestrator)" >&2
  exit 1
fi

# -e CI without a value passes the host's CI only when it is set (CI sets CI=true).
exec docker run --rm -u root -e CI -v "$PWD":/app -w /app --entrypoint sh "$IMAGE" -c '
  [ $# -gt 0 ] || set -- tests
  apk add -q --no-cache postgresql17 && pip install -q --root-user-action=ignore "pytest>=8" &&
  exec su -s /bin/sh nobody -c "HOME=/tmp PYTHONPATH=/app exec python -m pytest -q -p no:cacheprovider \"\$@\"" sh -- "$@"
' sh "$@"
