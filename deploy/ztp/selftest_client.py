"""Minimal DHCP and TFTP client for deploy/ztp/selftest.sh (runs in the orchestrator image).

    python selftest_client.py dhcp <mac>            -> "lease <ip> option66=<..> option67=<..>", exit 0
                                                       "no answer", exit 2
    python selftest_client.py tftp <server> <file>  -> the file, exit 0
                                                       "tftp error <code>: <message>", exit 1
                                                       "no answer", exit 2

The MAC goes in the DHCP packet (chaddr), which is what dnsmasq matches registrations on.
"""

import random
import socket
import struct
import sys

TIMEOUT = 6.0


def dhcp(mac: str) -> int:
    xid = random.getrandbits(32)
    chaddr = bytes.fromhex(mac.replace(":", "")) + b"\0" * 10
    packet = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, xid, 0, 0x8000,   # broadcast reply
                         b"\0" * 4, b"\0" * 4, b"\0" * 4, b"\0" * 4, chaddr, b"", b"")
    packet += b"\x63\x82\x53\x63" + bytes([53, 1, 1]) + bytes([55, 4, 1, 3, 66, 67]) + b"\xff"   # DISCOVER
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # nosemgrep: python.lang.security.audit.network.bind.avoid-bind-to-all-interfaces
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # Like a real DHCP client: tied to its interface, so the broadcast leaves without a route.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"eth0")
    # A DHCP client listens on every address (the self-test network only).
    sock.bind(("", 68))   # nosec B104
    sock.settimeout(TIMEOUT)
    for _ in range(3):
        sock.sendto(packet, ("255.255.255.255", 67))
        try:
            while True:
                data, _ = sock.recvfrom(4096)
                if len(data) < 240 or data[0] != 2 or struct.unpack("!I", data[4:8])[0] != xid:
                    continue
                options, i = {}, 240
                while i < len(data) and data[i] != 255:
                    if data[i] == 0:
                        i += 1
                        continue
                    code, length = data[i], data[i + 1]
                    options[code] = data[i + 2:i + 2 + length]
                    i += 2 + length
                text = lambda c: options.get(c, b"").rstrip(b"\0").decode(errors="replace")   # noqa: E731
                print(f"lease {socket.inet_ntoa(data[16:20])} option66={text(66)} option67={text(67)}")
                return 0
        except socket.timeout:
            continue
    print("no answer")
    return 2


def tftp(server: str, name: str, port: int = 69) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT)
    sock.sendto(b"\0\1" + name.encode() + b"\0octet\0", (server, port))
    data = b""
    while True:
        try:
            packet, addr = sock.recvfrom(4 + 512)
        except socket.timeout:
            print("no answer")
            return 2
        opcode = struct.unpack("!H", packet[:2])[0]
        if opcode == 5:
            code = struct.unpack("!H", packet[2:4])[0]
            message = packet[4:].rstrip(b"\0").decode(errors="replace")
            print(f"tftp error {code}: {message}")
            return 1
        if opcode != 3:
            continue
        data += packet[4:]
        sock.sendto(b"\0\4" + packet[2:4], addr)
        if len(packet) - 4 < 512:
            sys.stdout.write(data.decode(errors="replace"))
            return 0


if __name__ == "__main__":
    if sys.argv[1] == "dhcp":
        sys.exit(dhcp(sys.argv[2]))
    sys.exit(tftp(sys.argv[2], sys.argv[3]))
