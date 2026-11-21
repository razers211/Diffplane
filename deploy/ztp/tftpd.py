"""Read-only TFTP server for zero-touch provisioning.

dnsmasq's own TFTP served any client the folder of another switch when asked for it by path (self-test), and the first-boot configs hold the platform passwords. This server
serves a client only files directly in the folder named after its own address
(<root>/<client ip>/<name>): a name with a path or starting with "." is refused, and so is a
client with no folder (not registered). Remaining risk: a host deliberately taking a
registered switch's address before the switch boots.

    python3 tftpd.py <listen address> <root> [port]
"""

import os
import socket
import struct
import sys
import threading

BLOCK = 512
RETRIES = 5
TIMEOUT = 3.0
NOT_FOUND, ACCESS = 1, 2


def log(text: str) -> None:
    print(f"tftpd: {text}", flush=True)


def resolve(root: str, client: str, name: str) -> tuple[str | None, int, str]:
    """(path, 0, "") for a file the client may read, else (None, error code, message)."""
    if not name or "/" in name or "\\" in name or name.startswith(".") or "\0" in name:
        return None, ACCESS, "access violation"
    folder = os.path.join(root, client)
    if not os.path.isdir(folder):
        return None, ACCESS, "access violation"
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        return None, NOT_FOUND, "file not found"
    return path, 0, ""


def error_packet(code: int, message: str) -> bytes:
    return struct.pack("!HH", 5, code) + message.encode() + b"\0"


def send_file(path: str, client: tuple[str, int], bind_address: str) -> None:
    with open(path, "rb") as f:
        data = f.read()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_address, 0))
    sock.settimeout(TIMEOUT)
    try:
        block = 1
        while True:
            chunk = data[(block - 1) * BLOCK:block * BLOCK]
            packet = struct.pack("!HH", 3, block % 65536) + chunk
            for _ in range(RETRIES):
                sock.sendto(packet, client)
                try:
                    reply, addr = sock.recvfrom(4 + BLOCK)
                except socket.timeout:
                    continue
                if addr == client and reply[:2] == b"\0\4" and struct.unpack("!H", reply[2:4])[0] == block % 65536:
                    break
            else:
                log(f"{client[0]}: no acknowledgement, transfer of {path} abandoned")
                return
            if len(chunk) < BLOCK:
                log(f"sent {path} to {client[0]}")
                return
            block += 1
    finally:
        sock.close()


def handle(packet: bytes, client: tuple[str, int], root: str, sock: socket.socket, bind_address: str) -> None:
    opcode = struct.unpack("!H", packet[:2])[0] if len(packet) >= 2 else 0
    if opcode != 1:   # read requests only
        sock.sendto(error_packet(ACCESS, "read only"), client)
        return
    fields = packet[2:].split(b"\0")
    name = fields[0].decode(errors="replace") if fields else ""
    path, code, message = resolve(root, client[0], name)
    if path is None:
        log(f"refused {name!r} to {client[0]}: {message}")
        sock.sendto(error_packet(code, message), client)
        return
    threading.Thread(target=send_file, args=(path, client, bind_address), daemon=True).start()


def serve(address: str, root: str, port: int = 69, ready: threading.Event | None = None,
          stop: threading.Event | None = None) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((address, port))
    sock.settimeout(0.5)
    log(f"serving {root} on {address}:{port}")
    if ready is not None:
        ready.set()
    try:
        while stop is None or not stop.is_set():
            try:
                packet, client = sock.recvfrom(4 + BLOCK)
            except socket.timeout:
                continue
            handle(packet, client, root, sock, address)
    finally:
        sock.close()


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 69)
