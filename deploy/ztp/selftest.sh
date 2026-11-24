#!/bin/sh
# ZTP self-test (option 1): the ztp container (dnsmasq for DHCP, tftpd.py for TFTP) with the
# orchestrator's own config, on an isolated Docker bridge network (never the management LAN). Checks that
# a registered MAC gets its fixed address and can download only its own files, that an unknown MAC
# gets no lease, and that a host with no registration can read nothing, not even by path.
# Run from deploy/ after `docker compose build orchestrator ztp`:  sh ztp/selftest.sh
set -eu
ORCH="ghcr.io/razers211/diffplane-orchestrator:${DIFFPLANE_VERSION:-v0.1.3}"
ZTPIMG="ghcr.io/razers211/diffplane-ztp:${DIFFPLANE_VERSION:-v0.1.3}"
NET=ztp-selftest
VOL=ztp-selftest
KNOWN=02:00:00:00:00:01
UNKNOWN=02:00:00:00:00:02
cleanup() {
  docker rm -f ztp-selftest-dnsmasq >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
# A plain bridge, not --internal: Docker drops packets to addresses outside an internal
# network's subnet, which includes DHCP's 255.255.255.255 (request and reply). Broadcasts never
# leave a Docker bridge, and dnsmasq listens on this network's interface only.
docker network create --subnet 10.199.0.0/24 "$NET" >/dev/null
docker volume create "$VOL" >/dev/null

# The orchestrator's code writes the files, as for a real registration (no NetBox needed).
docker run --rm -u 0 -v "$VOL":/ztp --entrypoint python "$ORCH" -c '
import os
from pathlib import Path
from orchestrator import ztp
env = {"ZTP_DHCP_INTERFACE": "eth0", "ZTP_DHCP_NETWORK": "10.199.0.0/24", "ZTP_SERVER_ADDRESS": "10.199.0.2"}
reg = {"mac": "'$KNOWN'", "address": "10.199.0.10/24", "gateway": "10.199.0.1", "interface": "MEth0/0/0"}
root = Path("/ztp")
(root / "tftp" / "10.199.0.10").mkdir(parents=True)
(root / "tftp" / "10.199.0.10" / "ztp.ini").write_text(ztp.intermediate_file("selftest", reg, "10.199.0.2"))
(root / "tftp" / "10.199.0.11").mkdir()
(root / "tftp" / "10.199.0.11" / "other.cfg").write_text("not yours\n")
(root / "hosts").write_text("'$KNOWN',set:ztp_selftest,10.199.0.10\n")
(root / "opts").write_text("tag:ztp_selftest,3,10.199.0.1\ntag:ztp_selftest,66,10.199.0.2\ntag:ztp_selftest,67,ztp.ini\n")
(root / "dnsmasq.conf").write_text(ztp.dnsmasq_conf(env))
(root / "tftp-listen").write_text("10.199.0.2\n")
for p in root.rglob("*"):
    os.chmod(p, 0o755 if p.is_dir() else 0o644)
'

docker run -d --name ztp-selftest-dnsmasq --network "$NET" --ip 10.199.0.2 --cap-add NET_ADMIN \
  -v "$VOL":/ztp:ro "$ZTPIMG" >/dev/null
sleep 8

# Clients: a small Python DHCP/TFTP client in the orchestrator image (deploy/ztp/selftest_client.py).
# The test MAC goes inside the DHCP packet, which is what dnsmasq matches registrations on.
HERE=$(cd "$(dirname "$0")" && pwd)
client() {  # client <ip> <args...>: prints the client's output, returns its exit code
  ip=$1; shift
  docker run --rm -u 0 --network "$NET" --ip "$ip" -v "$HERE/selftest_client.py:/selftest_client.py:ro" \
    --entrypoint python "$ORCH" /selftest_client.py "$@"
}
check() {  # check <label> <expected exit code> <ip> <args...>
  label=$1; want=$2; shift 2
  set +e
  out=$(client "$@" 2>&1)
  got=$?
  set -e
  echo "  client: $(echo "$out" | head -n 3 | tr '\n' ' ')"
  if [ "$got" = "$want" ]; then echo "ok: $label"; return 0; fi
  echo "FAIL: $label (exit $got, expected $want)"; return 1
}
fail=0
check "registered MAC gets its fixed address and the ZTP options" 0 10.199.0.10 dhcp "$KNOWN" || fail=1
set +e; lease=$(client 10.199.0.10 dhcp "$KNOWN" 2>&1); set -e
echo "$lease" | grep -q "lease 10.199.0.10 option66=10.199.0.2 option67=ztp.ini" \
  && echo "ok: the lease is 10.199.0.10 with option 66 10.199.0.2 and option 67 ztp.ini" \
  || { echo "FAIL: unexpected lease: $lease"; fail=1; }
check "unknown MAC gets no answer" 2 10.199.0.20 dhcp "$UNKNOWN" || fail=1
check "the switch downloads its own intermediate file" 0 10.199.0.10 tftp 10.199.0.2 ztp.ini || fail=1
check "another switch's file is refused (not found in its own folder)" 1 10.199.0.10 tftp 10.199.0.2 other.cfg || fail=1
check "an unregistered host gets nothing, not even a plain name" 1 10.199.0.20 tftp 10.199.0.2 ztp.ini || fail=1
# A host with no folder of its own (not registered) asking for a registered switch's file by path.
set +e; out=$(client 10.199.0.20 tftp 10.199.0.2 10.199.0.10/ztp.ini 2>&1); got=$?; set -e
echo "  client: $(echo "$out" | head -n 1)"
case $got in
  0) echo "WARNING: an unregistered host can read a registered switch's files by naming their folder" ;;
  1) echo "ok: an unregistered host cannot read a registered switch's files" ;;
  *) echo "FAIL: no TFTP answer to the unregistered host"; fail=1 ;;
esac
docker logs ztp-selftest-dnsmasq 2>&1 | tail -n 30
exit $fail
