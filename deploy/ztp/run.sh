#!/bin/sh
# Runs dnsmasq (DHCP) and tftpd.py (TFTP) with the files the orchestrator writes into /ztp, and
# restarts them whenever those change (a switch registered, unregistered or activated). An empty
# /ztp/dnsmasq.conf means ZTP is not configured (ZTP_DHCP_INTERFACE, ZTP_SERVER_ADDRESS,
# ZTP_DHCP_NETWORK): both stay off.
set -u
state() { cat /ztp/dnsmasq.conf /ztp/hosts /ztp/opts /ztp/tftp-listen 2>/dev/null | md5sum; }
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
dhcp=""
tftp=""
last=""
while true; do
  now=$(state)
  if [ "$now" != "$last" ] || { [ -n "$dhcp" ] && ! alive "$dhcp"; } || { [ -n "$tftp" ] && ! alive "$tftp"; }; then
    for pid in $dhcp $tftp; do
      if alive "$pid"; then kill "$pid"; wait "$pid" 2>/dev/null; fi
    done
    dhcp=""
    tftp=""
    if [ -s /ztp/dnsmasq.conf ]; then
      echo "ztp: starting dnsmasq and tftpd"
      dnsmasq --keep-in-foreground --log-facility=- --conf-file=/ztp/dnsmasq.conf &
      dhcp=$!
      python3 /tftpd.py "$(head -n 1 /ztp/tftp-listen)" /ztp/tftp &
      tftp=$!
    else
      echo "ztp: not configured, dnsmasq and tftpd off"
    fi
    last=$now
  fi
  sleep 5
done
