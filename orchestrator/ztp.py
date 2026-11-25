"""Zero-touch provisioning of new switches.

1. Register: a planned device gets its MEth MAC, management address and gateway (device custom
   field `ztp`). The orchestrator writes, into the ZTP volume (`ZTP_DIR`):
   - dnsmasq's config, hosts and options files: DHCP answers only registered MACs, each with
     its fixed address (no pool), with the TFTP server (option 66) and the intermediate file
     name (option 67);
   - per switch, in a folder named after its address: the intermediate file and the first-boot
     config, served over TFTP by deploy/ztp/tftpd.py to that address only.
2. The switch boots blank, gets its address by DHCP, downloads its files and starts with its
   management address, SSH and NETCONF on and the platform's accounts.
3. Watch: the orchestrator waits until the switch answers SSH at its address and fetches its
   host keys (ports 22 and 830) into the registration, shown in the GUI as pending.
4. Approve: an approver compares the fingerprints with the switch's console and approves; the
   keys are fetched again and must be exactly the approved ones; then they are pinned, the device
   is activated with its management address, the registration is cleared and its files deleted.

The first-boot config is not an Annet generator: it is written once to a blank switch, never
deployed or diffed, so the platform still owns nothing of the management plane afterwards.
It holds the platform passwords in clear (TFTP on the management network) and
is deleted once the switch is activated; the API only shows it redacted.

Unverified until a blank switch exists (option 1): which DHCP options V300 ZTP reads,
the intermediate file format below, and whether plain passwords in a startup file are accepted.
The bring-up lines themselves are the ones a test switch shows.
"""

import asyncio
import ipaddress
import os
import re
import shutil
import socket
import time
from pathlib import Path

from orchestrator import hostkeys
from orchestrator.lifecycle import Lifecycle
from generators import naming
from orchestrator.netbox import NetBoxClient
from orchestrator.redact import redact
from orchestrator.segments import SegmentError

FIELD = "ztp"
INTERMEDIATE = "ztp.ini"
ACCOUNTS = (("GNETCLI_DEV_LOGIN", "GNETCLI_DEV_PASSWORD"), ("COLLECTOR_LOGIN", "COLLECTOR_PASSWORD"),
            ("ZTP_ADMIN_LOGIN", "ZTP_ADMIN_PASSWORD"))
# The SSH settings a test switch runs with (bring-up config).
SSH_LINES = (
    "stelnet server enable",
    "snetconf server enable",
    "netconf",
    " protocol inbound ssh port 830",
    "ssh server-source -i {interface}",
    "undo ssh server-source all-interface",
    "undo ssh ipv6 server-source all-interface",
    "ssh server rsa-key min-length 3072",
    "ssh authorization-type default aaa",
    "ssh server cipher aes256_gcm aes128_gcm aes256_ctr aes192_ctr aes128_ctr",
    "ssh server hmac sha2_512 sha2_256",
    "ssh server key-exchange dh_group_exchange_sha256 dh_group16_sha512 curve25519_sha256",
)
LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z")


def normalize_mac(text: str) -> str:
    """aa:bb:cc:dd:ee:ff from any common form (aa-bb-..., aabb-ccdd-eeff as VRP shows it)."""
    digits = re.sub(r"[^0-9a-fA-F]", "", text or "")
    if len(digits) != 12 or re.sub(r"[0-9a-fA-F:.\-]", "", text or ""):
        raise SegmentError(f"{text!r} is not a MAC address")
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)).lower()


def vrp_mac(mac: str) -> str:
    digits = mac.replace(":", "")
    return "-".join(digits[i:i + 4] for i in range(0, 12, 4))


def accounts(env=os.environ) -> list[tuple[str, str]]:
    """The accounts the first-boot config creates: Annet's, the collector's, an admin for people."""
    found: dict[str, str] = {}
    for login_var, password_var in ACCOUNTS:
        login, password = env.get(login_var, ""), env.get(password_var, "")
        if not login:
            continue
        if not LOGIN.match(login):
            raise SegmentError(f"{login_var} is not a valid local user name")
        if not password:
            raise SegmentError(f"{password_var} is not set")
        if found.get(login, password) != password:
            raise SegmentError(f"{login} is set twice with different passwords")
        found[login] = password
    if not found:
        raise SegmentError("no platform account is set (GNETCLI_DEV_LOGIN, COLLECTOR_LOGIN, ZTP_ADMIN_LOGIN)")
    return sorted(found.items())


def first_boot_config(name: str, reg: dict, users: list[tuple[str, str]]) -> str:
    """The switch's startup config: hostname, accounts, management address and route, SSH/NETCONF."""
    address = ipaddress.IPv4Interface(reg["address"])
    lines = [f"sysname {name}", "#", "aaa"]
    for login, password in users:
        lines += [f" local-user {login} password irreversible-cipher {password}",
                  f" local-user {login} password-force-change disable",
                  f" local-user {login} privilege level 3",
                  f" local-user {login} service-type ssh"]
    lines += ["#", f"interface {reg['interface']}", " ip binding vpn-instance _management_vpn_",
              f" ip address {address.ip} {address.netmask}", "#",
              f"ip route-static vpn-instance _management_vpn_ 0.0.0.0 0.0.0.0 {reg['gateway']}", "#"]
    lines += [line.format(interface=reg["interface"]) for line in SSH_LINES]
    return "\n".join(lines + ["#", "return"]) + "\n"


def intermediate_file(name: str, reg: dict, server: str) -> str:
    """The ZTP intermediate file (INI form) naming the switch's startup config. Unverified on V300."""
    return "\n".join([
        ";BEGIN ROUTER",
        "[GLOBAL CONFIG]",
        f"FILESERVER=tftp://{server}/",
        "[DEVICE0 DESCRIPTION]",
        f"MAC={vrp_mac(reg['mac'])}",
        "DEVICETYPE=DEFAULT",
        f"SYSTEM-CONFIG={name}.cfg",
        ";END ROUTER",
    ]) + "\n"


def dnsmasq_conf(env=os.environ) -> str:
    """dnsmasq's main config, or "" (dnsmasq not started) while ZTP is not configured."""
    interface, server, network = (env.get(k, "") for k in ("ZTP_DHCP_INTERFACE", "ZTP_SERVER_ADDRESS",
                                                           "ZTP_DHCP_NETWORK"))
    if not (interface and server and network):
        return ""
    net = ipaddress.IPv4Network(network)
    return "\n".join([
        "# Written by the orchestrator (orchestrator/ztp.py). DHCP only for registered switches.",
        "port=0",                                  # no DNS
        f"interface={interface}",
        "bind-interfaces",
        f"dhcp-range={net.network_address},static,{net.netmask}",   # fixed addresses only, never a pool
        "dhcp-ignore=tag:!known",                  # unknown MACs get no answer at all
        "dhcp-hostsfile=/ztp/hosts",
        "dhcp-optsfile=/ztp/opts",
        # No TFTP here: dnsmasq served another switch's folder when asked by path
        # (self-test); deploy/ztp/tftpd.py serves each address only its own folder.
        "log-dhcp",
    ]) + "\n"


def window_seconds(env=os.environ) -> float:
    """How long a registration is served (DHCP and TFTP): ZTP_WINDOW_HOURS, 24 by default."""
    return float(env.get("ZTP_WINDOW_HOURS") or 24) * 3600


def expired(reg: dict, env=os.environ, at: float | None = None) -> bool:
    """Past its provisioning window and not yet booted: its files are no longer served.
    Registering the switch again starts a new window."""
    return not reg.get("offered") and (at or time.time()) - reg.get("registered_at", 0) > window_seconds(env)


class Ztp:
    def __init__(self, netbox: NetBoxClient, directory: str | None = None, env=os.environ):
        self.nb = netbox
        self.env = env
        self.dir = Path(directory or env.get("ZTP_DIR", "") or "/nonexistent")

    # -- registrations -----------------------------------------------------------------------

    async def _device(self, name: str) -> dict:
        device = await self.nb.find_one("dcim/devices", name=name)
        if device is None:
            raise SegmentError(f"no device {name!r}")
        return device

    async def registrations(self) -> dict[str, dict]:
        return {d["name"]: (d.get("custom_fields") or {})[FIELD]
                for d in await self.nb.find("dcim/devices") if (d.get("custom_fields") or {}).get(FIELD)}

    async def served(self) -> dict[str, dict]:
        """The registrations inside their provisioning window: the only ones DHCP and TFTP answer."""
        return {name: reg for name, reg in (await self.registrations()).items() if not expired(reg, self.env)}

    async def register(self, name: str, mac: str, address: str, gateway: str, interface: str = "MEth0/0/0") -> dict:
        accounts(self.env)   # its first-boot config needs them: refuse before writing anything
        device = await self._device(name)
        status = device.get("status")
        if (status.get("value") if isinstance(status, dict) else status) != "planned":
            raise SegmentError(f"{name} is active: only a planned switch is provisioned")
        if not naming.is_management(interface):
            raise SegmentError(f"{interface!r} is not a management port (MEth0/0/0, Management1)")
        mac = normalize_mac(mac)
        try:
            addr = ipaddress.IPv4Interface(address)
            gw = ipaddress.IPv4Address(gateway)
        except ValueError as exc:
            raise SegmentError(f"invalid address: {exc}") from exc
        if "/" not in address or addr.network.prefixlen >= 31 or addr.ip in (addr.network.network_address,
                                                                             addr.network.broadcast_address):
            raise SegmentError("give the management address with its mask, e.g. 172.24.0.110/24")
        if gw not in addr.network or gw == addr.ip:
            raise SegmentError("the gateway must be another address in the management subnet")
        network = self.env.get("ZTP_DHCP_NETWORK")
        if network and addr.ip not in ipaddress.IPv4Network(network):
            raise SegmentError(f"{addr.ip} is outside the ZTP DHCP network {network}")
        for other, reg in (await self.registrations()).items():
            if other == name:
                continue
            if reg["mac"] == mac:
                raise SegmentError(f"MAC {mac} is already registered for {other}")
            if ipaddress.IPv4Interface(reg["address"]).ip == addr.ip:
                raise SegmentError(f"{addr.ip} is already registered for {other}")
        for ip in await self.nb.find("ipam/ip-addresses", address=str(addr.ip)):
            if ip.get("vrf") is None:
                raise SegmentError(f"{addr.ip} is already a management address in NetBox")
        reg = {"mac": mac, "address": str(addr), "gateway": str(gw), "interface": interface,
               "registered_at": int(time.time())}
        await self.nb.update("dcim/devices", device["id"], {"custom_fields": {FIELD: reg}})
        await self.publish()
        return {"device": name, **reg}

    async def unregister(self, name: str) -> dict:
        device = await self._device(name)
        if not (device.get("custom_fields") or {}).get(FIELD):
            raise SegmentError(f"{name} is not registered for ZTP")
        await self.nb.update("dcim/devices", device["id"], {"custom_fields": {FIELD: None}})
        await self.publish()
        return {"device": name}

    async def preview(self, name: str) -> dict:
        """The files a registered switch would get, secrets masked."""
        reg = (await self.registrations()).get(name)
        if reg is None:
            raise SegmentError(f"{name} is not registered for ZTP")
        server = self.env.get("ZTP_SERVER_ADDRESS") or "<ZTP_SERVER_ADDRESS>"
        masked = [(login, "******") for login, _ in accounts(self.env)]
        return {"device": name, "intermediate": intermediate_file(name, reg, server),
                "config": redact(first_boot_config(name, reg, masked))}

    # -- files for dnsmasq -------------------------------------------------------------------

    async def publish(self) -> bool:
        """Rewrite dnsmasq's files and the per-switch folders from the registrations. False when
        no ZTP volume is mounted (ZTP_DIR); registrations are kept either way."""
        if not self.dir.is_dir():
            return False
        regs = await self.served()
        server = self.env.get("ZTP_SERVER_ADDRESS", "")
        users = accounts(self.env) if regs else []
        tftp = self.dir / "tftp"
        tftp.mkdir(exist_ok=True)
        hosts, opts = [], []
        wanted = set()
        for name, reg in sorted(regs.items()):
            ip = str(ipaddress.IPv4Interface(reg["address"]).ip)
            tag = "ztp_" + re.sub(r"[^A-Za-z0-9]", "_", name)
            hosts.append(f"{reg['mac']},set:{tag},{ip}")
            opts += [f"tag:{tag},3,{reg['gateway']}", f"tag:{tag},66,{server}", f"tag:{tag},67,{INTERMEDIATE}"]
            folder = tftp / ip
            folder.mkdir(exist_ok=True)
            _write(folder / INTERMEDIATE, intermediate_file(name, reg, server))
            _write(folder / f"{name}.cfg", first_boot_config(name, reg, users))
            wanted.add(ip)
        for folder in tftp.iterdir():   # switches no longer registered: their files go
            if folder.is_dir() and folder.name not in wanted:
                shutil.rmtree(folder)
        _write(self.dir / "hosts", "".join(line + "\n" for line in hosts))
        _write(self.dir / "opts", "".join(line + "\n" for line in opts))
        conf = dnsmasq_conf(self.env)
        _write(self.dir / "dnsmasq.conf", conf)
        _write(self.dir / "tftp-listen", self.env.get("ZTP_SERVER_ADDRESS", "") + "\n" if conf else "")
        return True

    # -- watching and approving --------------------------------------------------------------

    async def watch_once(self, probe=None, fetch=None) -> list[str]:
        """Fetch the host keys of registered switches that now answer SSH; returns their names."""
        probe = probe or _answers_ssh
        reported = []
        for name, reg in (await self.served()).items():
            if reg.get("offered"):
                continue
            ip = str(ipaddress.IPv4Interface(reg["address"]).ip)
            if not await asyncio.to_thread(probe, ip):
                continue
            try:
                offered = await _fetch(ip, fetch)
            except hostkeys.HostKeyError:
                continue   # booting: SSH up before its keys are readable; try again next round
            device = await self._device(name)
            await self.nb.update("dcim/devices", device["id"], {"custom_fields": {FIELD: {
                **reg, "offered": {str(p): k for p, k in offered.items()}, "reported_at": int(time.time())}}})
            reported.append(name)
        return reported

    async def approve(self, name: str, keys: list[str], fetch=None) -> dict:
        """Pin the approved keys (fetched again, must be exactly these), activate, clean up."""
        reg = (await self.registrations()).get(name)
        if reg is None:
            raise SegmentError(f"{name} is not registered for ZTP")
        given = sorted({hostkeys.parse_key(k) for k in keys})
        if not given:
            raise SegmentError("no keys approved")
        ip = str(ipaddress.IPv4Interface(reg["address"]).ip)
        try:
            fetched = await _fetch(ip, fetch)
            new = hostkeys.plan_pin(fetched, given)
        except hostkeys.HostKeyError as exc:
            raise SegmentError(str(exc)) from exc
        if sorted({k for v in new.values() for k in v}) != given:
            raise SegmentError("the switch no longer offers the keys you approved; wait for a new report")
        await Lifecycle(self.nb).activate(name, reg["address"], reg["interface"], allow_ztp=True)
        device = await self._device(name)
        await self.nb.update("dcim/devices", device["id"], {"custom_fields": {
            hostkeys.FIELD: {str(p): new[p] for p in hostkeys.PORTS}, FIELD: None}})
        await self.publish()
        return {"device": name, "address": reg["address"],
                "fingerprints": [hostkeys.fingerprint(k) for k in given]}


async def _fetch(ip: str, fetch=None) -> dict[int, list[str]]:
    fetch = fetch or hostkeys.fetch_keys
    offered = {port: sorted((await asyncio.to_thread(fetch, ip, port)).keys) for port in hostkeys.PORTS}
    hostkeys.plan_pin(offered, [])   # every port must offer a key
    return offered


def _answers_ssh(ip: str, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, hostkeys.SSH_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


class ZtpWatcher:
    """Background task: every ZTP_WATCH_INTERVAL seconds (default 30), report the keys of
    registered switches that came up."""

    def __init__(self, netbox: NetBoxClient, interval: float | None = None):
        self.ztp = Ztp(netbox)
        self.interval = interval or float(os.environ.get("ZTP_WATCH_INTERVAL", "30"))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        try:
            await self.ztp.publish()   # dnsmasq's files match NetBox after a restart
        except Exception as exc:   # noqa: BLE001 - a broken volume must not stop the orchestrator
            print(f"ztp: cannot write the dnsmasq files: {exc}")
        while True:
            try:
                await self.ztp.watch_once()
                await self.ztp.publish()   # registrations past their window: files deleted, DHCP silent
            except Exception as exc:   # noqa: BLE001
                print(f"ztp: watch failed: {exc}")
            await asyncio.sleep(self.interval)
