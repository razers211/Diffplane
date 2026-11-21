"""SSH host keys of the switches, pinned in NetBox.

Annet (gnetcli, SSH on port 22) and the collector (NETCONF on port 830) only talk to a
switch whose host key is pinned on its NetBox device, custom field `ssh_host_keys`:
    {"22": ["<type> <base64>", ...], "830": ["<type> <base64>", ...]}
Every key type the switch offers is pinned, since clients may negotiate any of them.

A pin is written only after a person has compared the fingerprints with the switch's
console: by this command, or from the GUI (device page, an approver pins). The
orchestrator is the only writer to NetBox:

    python -m orchestrator.hostkeys show <device>
    python -m orchestrator.hostkeys pin <device> [--key "<type> <base64>" ...]
    python -m orchestrator.hostkeys forget <device>

A device without a pin is refused. A replaced switch, under the same name or on a
reused IP, presents another key and is refused until it is pinned again (docs/operations.md).
"""

import argparse
import asyncio
import base64
import hashlib
import logging
import os
import socket
import secrets
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

FIELD = "ssh_host_keys"
SSH_PORT = 22         # gnetcli (annet fetch and deploy)
NETCONF_PORT = 830    # collector
PORTS = (SSH_PORT, NETCONF_PORT)
# Host key algorithm -> the key it is signed with. The three RSA algorithms share one key.
FETCHABLE = {
    "ssh-ed25519": "ssh-ed25519",
    "ecdsa-sha2-nistp256": "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384": "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521": "ecdsa-sha2-nistp521",
    "rsa-sha2-512": "ssh-rsa",
    "rsa-sha2-256": "ssh-rsa",
    "ssh-rsa": "ssh-rsa",
}
# Seconds between connections: rapid NETCONF connections were reset by a test switch.
CONNECT_PAUSE = 1.0
RETRY_PAUSE = 3.0
# Read by our gnetcli patch (deploy/gnetcli/known-hosts.patch); compose sets it.
KNOWN_HOSTS_ENV = "KNOWN_HOSTS_FILE"


class HostKeyError(Exception):
    pass


def parse_key(text: str) -> str:
    """Normalise "<type> <base64> [comment]" to "<type> <base64>"; the type must match the key."""
    parts = text.split()
    if len(parts) < 2:
        raise HostKeyError(f"not a public key (expected '<type> <base64>'): {text!r}")
    kind, data = parts[0], parts[1]
    try:
        blob = base64.b64decode(data, validate=True)
        (length,) = struct.unpack(">I", blob[:4])
        embedded = blob[4:4 + length].decode()
    except (ValueError, struct.error, UnicodeDecodeError) as exc:
        raise HostKeyError(f"not a valid {kind} key: {exc}") from exc
    if embedded != kind:
        raise HostKeyError(f"key type {kind} does not match the key itself ({embedded})")
    return f"{kind} {data}"


def fingerprint(key: str) -> str:
    """OpenSSH's SHA256 fingerprint, as `ssh-keygen -l` prints it."""
    blob = base64.b64decode(key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def pins(device: dict) -> dict[int, list[str]]:
    stored = (device.get("custom_fields") or {}).get(FIELD) or {}
    return {port: list(stored.get(str(port)) or []) for port in PORTS}


def address(device: dict) -> str | None:
    ip = (device.get("primary_ip4") or {}).get("address")
    return ip.split("/")[0] if ip else None


def known_hosts_lines(devices: list[dict]) -> list[str]:
    """OpenSSH known_hosts lines for the pinned devices (port 22 bare, others [ip]:port)."""
    lines = []
    for device in sorted(devices, key=lambda d: d["name"]):
        ip = address(device)
        if ip is None:
            continue
        for port, keys in pins(device).items():
            host = ip if port == SSH_PORT else f"[{ip}]:{port}"
            lines += [f"{host} {key}" for key in keys]
    return lines


def md5_fingerprint(key: str) -> str:
    """The colon-separated MD5 fingerprint ncclient hands to its unknown-host callback."""
    # MD5 is what ncclient hands over; faking a pinned key would need a second preimage, not a collision.
    digest = hashlib.md5(base64.b64decode(key.split()[1])).hexdigest()  # nosec B324  # nosemgrep
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def netconf_check(device: dict):
    """The ncclient unknown-host callback for a device: accept only its pinned NETCONF keys.

    ncclient's own pinning (`hostkey_b64`) negotiates only the key's type name, `ssh-rsa`
    for RSA, which V300 refuses (it offers rsa-sha2-512/256 only).
    So the collector loads no known_hosts and decides here, before logging in. ncclient
    only passes the MD5 fingerprint; matching it would take an MD5 second preimage of the
    pinned key, which is not practical. None if the device has no NETCONF pin.
    """
    allowed = {md5_fingerprint(key) for key in pins(device)[NETCONF_PORT]}
    if not allowed:
        return None
    return lambda host, fingerprint: fingerprint in allowed


async def write_known_hosts(netbox, path: Path) -> None:
    """Rewrite the known_hosts file gnetcli reads, from the pins of the active devices."""
    devices = await netbox.find("dcim/devices", status="active", has_primary_ip="true")
    text = "".join(line + "\n" for line in known_hosts_lines(devices))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")   # runs may rewrite it at once
    tmp.write_text(text)
    os.replace(tmp, path)   # gnetcli reads it per connection: never a half-written file


class Guard:
    """Called before every Annet run that connects to a device (diff, patch, deploy)."""

    def __init__(self, netbox, path: str | None = None):
        self.netbox = netbox
        self.path = path if path is not None else os.environ.get(KNOWN_HOSTS_ENV)

    async def __call__(self, device: str) -> None:
        from orchestrator.annet import AnnetError
        from orchestrator.netbox import NetBoxError

        if not self.path:
            raise AnnetError(f"{KNOWN_HOSTS_ENV} is not set: SSH host keys would not be verified")
        try:
            found = await self.netbox.find_one("dcim/devices", name=device)
            if found is None or not pins(found)[SSH_PORT]:
                raise AnnetError(f"{device} has no pinned SSH host key; pin it first: "
                                 f"python -m orchestrator.hostkeys pin {device} (docs/operations.md)")
            await write_known_hosts(self.netbox, Path(self.path))
        except NetBoxError as exc:
            raise AnnetError(f"cannot check {device}'s SSH host key: {exc}") from exc


# -- fetching keys from a switch (pin command only) -----------------------------------------

@dataclass
class Offer:
    """What a server offers on one port: its host keys and every host key algorithm it lists."""

    keys: list[str]
    algorithms: list[str] = field(default_factory=list)

    @property
    def unsupported(self) -> list[str]:
        return [a for a in self.algorithms if a not in FETCHABLE]


def parse_kexinit(payload: bytes) -> list[str]:
    """The server_host_key_algorithms name-list of an SSH_MSG_KEXINIT payload (RFC 4253, 7.1)."""
    if not payload or payload[0] != 20:
        raise HostKeyError("the server did not start the key exchange")
    pos = 1 + 16   # message number, cookie
    lists = []
    for _ in range(2):   # kex_algorithms, server_host_key_algorithms
        (length,) = struct.unpack(">I", payload[pos:pos + 4])
        pos += 4
        lists.append(payload[pos:pos + length].decode())
        pos += length
    return [name for name in lists[1].split(",") if name]


def offered_algorithms(host: str, port: int, timeout: float) -> list[str]:
    """Read the server's first key-exchange message; it lists its host key algorithms in clear."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        stream = sock.makefile("rb")
        sock.sendall(b"SSH-2.0-fabric_hostkeys\r\n")
        while True:
            line = stream.readline(512)
            if not line:
                raise ConnectionResetError("closed before the SSH banner")
            if line.startswith(b"SSH-"):
                break
        length, padding = struct.unpack(">IB", stream.read(5))
        body = stream.read(length - 1)
        return parse_kexinit(body[:length - 1 - padding])


def _fetch_one(host: str, port: int, algorithm: str, timeout: float) -> str:
    import paramiko

    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.get_security_options().key_types = (algorithm,)
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return f"{key.get_name()} {key.get_base64()}"
    finally:
        transport.close()


def _retry(what: str, call):
    """One retry after a pause: VRP resets connections that come too fast."""
    import paramiko

    for attempt in (1, 2):
        try:
            return call()
        except (OSError, EOFError, paramiko.SSHException) as exc:
            if attempt == 2:
                raise HostKeyError(f"{what}: {type(exc).__name__}: {exc}") from exc
            time.sleep(RETRY_PAUSE)


def fetch_keys(host: str, port: int, timeout: float = 10.0) -> Offer:
    """Every host key the server offers, without logging in.

    One connection reads the algorithms the server lists, then one per key it has. The keys
    are not trusted by fetching them: a person compares their fingerprints with the
    switch's console before they are pinned.
    """
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)   # it logs every refused handshake
    where = f"{host} port {port}"
    algorithms = _retry(f"cannot read the host key algorithms of {where}",
                        lambda: offered_algorithms(host, port, timeout))
    wanted: dict[str, str] = {}   # key type -> first algorithm the server lists for it
    for algorithm in algorithms:
        if algorithm in FETCHABLE:
            wanted.setdefault(FETCHABLE[algorithm], algorithm)
    keys = []
    for algorithm in wanted.values():
        time.sleep(CONNECT_PAUSE)
        keys.append(_retry(f"cannot fetch the {algorithm} key of {where}",
                           lambda: _fetch_one(host, port, algorithm, timeout)))
    return Offer(sorted(set(keys)), algorithms)


def plan_pin(fetched: dict[int, list[str]], given: list[str], ports=PORTS) -> dict[int, list[str]]:
    """The pin to write. With keys given (copied from the console), every key the switch
    offers must be one of them; otherwise the fetched keys are pinned once confirmed."""
    for port in ports:
        if not fetched.get(port):
            raise HostKeyError(f"the switch offered no host key on port {port}")
    if given:
        trusted = set(given)
        unknown = sorted({key for keys in fetched.values() for key in keys} - trusted)
        if unknown:
            raise HostKeyError("the switch offers keys you did not give: "
                               + ", ".join(f"{k.split()[0]} {fingerprint(k)}" for k in unknown))
    return {port: sorted(fetched[port]) for port in ports}


def describe(pinned: dict[int, list[str]]) -> str:
    lines = []
    for port in PORTS:
        name = "SSH" if port == SSH_PORT else "NETCONF"
        lines.append(f"  port {port} ({name}):")
        lines += [f"    {key.split()[0]:<22} {fingerprint(key)}" for key in pinned.get(port) or []] or ["    (none)"]
    if set(pinned.get(SSH_PORT) or []) != set(pinned.get(NETCONF_PORT) or []):
        lines.append("  note: ports 22 and 830 present different keys")
    return "\n".join(lines)


# -- from the GUI -----------------------------------------------------
# The same steps as `pin`: the GUI shows what the switch offers, a person compares the
# fingerprints with the switch's own display and approves; the approval names the exact keys,
# and the switch is fetched again so they are pinned only if it still offers exactly those.

def key_info(key: str) -> dict:
    return {"key": key, "type": key.split()[0], "sha256": fingerprint(key), "md5": md5_fingerprint(key)}


async def _fetch_all(device: dict, fetch) -> dict[int, Offer]:
    ip = address(device)
    if ip is None:
        raise HostKeyError(f"{device['name']} has no management address: activate it first")
    fetch = fetch or fetch_keys   # looked up at call time (tests replace it)
    return {port: await asyncio.to_thread(fetch, ip, port) for port in PORTS}


async def offer(netbox, name: str, fetch=None) -> dict:
    """The host keys a switch offers now, with fingerprints, next to what is pinned."""
    device = await _device(netbox, name)
    offers = await _fetch_all(device, fetch)
    fetched = {port: sorted(o.keys) for port, o in offers.items()}
    plan_pin(fetched, [])   # refuses a port that offers no key
    current = pins(device)
    return {
        "device": name,
        "address": address(device),
        "offered": {str(port): [key_info(k) for k in keys] for port, keys in fetched.items()},
        "pinned": {str(port): [key_info(k) for k in keys] for port, keys in current.items()},
        "unchanged": current == fetched,
        "same_on_both_ports": set(fetched[SSH_PORT]) == set(fetched[NETCONF_PORT]),
        "unsupported": {str(port): o.unsupported for port, o in offers.items() if o.unsupported},
    }


async def pin_approved(netbox, name: str, keys: list[str], fetch=None) -> dict:
    """Pin the keys a person approved, if the switch still offers exactly these."""
    device = await _device(netbox, name)
    given = sorted({parse_key(k) for k in keys})
    if not given:
        raise HostKeyError("no keys approved")
    offers = await _fetch_all(device, fetch)
    fetched = {port: o.keys for port, o in offers.items()}
    new = plan_pin(fetched, given)   # refuses keys the switch offers that were not approved
    offered = sorted({k for v in new.values() for k in v})
    if offered != given:
        raise HostKeyError("the switch no longer offers the keys you approved; fetch them again")
    await netbox.update("dcim/devices", device["id"], {"custom_fields": {FIELD: {str(port): new[port] for port in PORTS}}})
    return {"device": name, "address": address(device), "fingerprints": [fingerprint(k) for k in offered]}


async def forget_pins(netbox, name: str) -> dict:
    device = await _device(netbox, name)
    await netbox.update("dcim/devices", device["id"], {"custom_fields": {FIELD: None}})
    return {"device": name}


# -- command line ---------------------------------------------------------------------------

async def _device(netbox, name: str) -> dict:
    device = await netbox.find_one("dcim/devices", name=name)
    if device is None:
        raise HostKeyError(f"no device {name!r} in NetBox")
    return device


async def _audit(action: str, detail: str) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return
    from orchestrator import db
    from orchestrator.auth import audit

    engine = db.make_engine(url, pooled=False)
    try:
        await audit(db.make_sessionmaker(engine), None, action, detail=detail)
    finally:
        await engine.dispose()


async def run(action: str, name: str, keys: list[str], confirm=input) -> int:
    from orchestrator.config import Settings
    from orchestrator.netbox import NetBoxClient

    settings = Settings.from_env()
    netbox = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
    try:
        return await command(netbox, action, name, keys, confirm)
    finally:
        await netbox.aclose()


async def command(netbox, action: str, name: str, keys: list[str], confirm=input, fetch=fetch_keys,
                  audit=_audit) -> int:
    device = await _device(netbox, name)
    current = pins(device)
    if action == "show":
        print(f"{name} ({address(device) or 'no management address'}):")
        print(describe(current))
        return 0
    if action == "forget":
        await netbox.update("dcim/devices", device["id"], {"custom_fields": {FIELD: None}})
        await audit("hostkey.forget", name)
        print(f"{name}: pin removed; Annet and the collector refuse it until it is pinned again")
        return 0

    ip = address(device)
    if ip is None:
        raise HostKeyError(f"{name} has no management address (primary IP) in NetBox")
    given = [parse_key(k) for k in keys]
    offers = {port: await asyncio.to_thread(fetch, ip, port) for port in PORTS}
    for port, offer in offers.items():
        print(f"port {port} lists host key algorithms: {', '.join(offer.algorithms) or '(none)'}")
        if offer.unsupported:
            print(f"  warning: cannot read {', '.join(offer.unsupported)}; those are not pinned")
    new = plan_pin({port: offer.keys for port, offer in offers.items()}, given)
    print(f"{name} ({ip}) offers:")
    print(describe(new))
    if any(current.values()):
        print("currently pinned:")
        print(describe(current))
        if current == new:
            print("unchanged: nothing to do")
            return 0
    if not given:
        print("\nCompare these fingerprints with the switch's own display of its host keys, on its console.")
    answer = confirm('Type "yes" to pin them: ')
    if answer.strip() != "yes":
        print("not pinned")
        return 1
    await netbox.update("dcim/devices", device["id"],
                        {"custom_fields": {FIELD: {str(port): new[port] for port in PORTS}}})
    await audit("hostkey.pin", f"{name} {ip} " + " ".join(sorted({fingerprint(k) for v in new.values() for k in v})))
    print(f"{name}: pinned")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.hostkeys",
                                     description="Pin switches' SSH host keys in NetBox")
    parser.add_argument("action", choices=["show", "pin", "forget"])
    parser.add_argument("device")
    parser.add_argument("--key", action="append", default=[],
                        help='a host key copied from the switch console, "<type> <base64>" (repeatable)')
    args = parser.parse_args()
    from orchestrator.netbox import NetBoxError

    try:
        return asyncio.run(run(args.action, args.device, args.key))
    except (HostKeyError, NetBoxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
