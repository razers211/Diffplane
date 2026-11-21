"""Import sessions: addresses -> host keys approved -> switches read ->
preview -> fabric written.

1. The operator gives the switches' management addresses (with mask); the orchestrator
   fetches the host keys each offers on ports 22 and 830 (as the device page does).
2. An approver compares the fingerprints with each switch's console and approves exactly
   those keys; they are fetched again and must be the same (orchestrator/hostkeys.py).
3. Each approved switch is read with `display` commands only, NETCONF first (orchestrator/ssh_read.py),
   the pinned key checked before login, with the collector's read account.
4. The preview (orchestrator/import_preview.py, its own process) shows the fabric and the patch
   each switch would get; nothing is written.
5. Import writes the fabric to NetBox (orchestrator/import_build.py): devices active with
   their management address and the approved pins. Deploys stay off for it
   (DEPLOY_ALLOWED_SITES); drift shows its diffs.

New fabric and Add switches use the same sessions for steps 1 and 2, then `detect` reads each
approved switch's model and ports over NETCONF (orchestrator/switch_inventory.py) so it is built
with them; the builder pins the approved keys when it writes the switch.

Sessions live in this process's memory only: the switches' outputs hold secrets and are never
stored or returned. A restart drops them (start again).
"""

import asyncio
import ipaddress
import json
import os
import sys
import uuid

from orchestrator import hostkeys, import_build, ssh_read, switch_inventory
from orchestrator.fabric_import import ImportError_, assemble
from orchestrator.import_parse import ImportParseError, parse_switch
from orchestrator.segments import SegmentError

COMMANDS = ["display version", "display lldp neighbor brief", "display current-configuration"]
PREVIEW_TIMEOUT = 300
MAX_SWITCHES = 200


class Imports:
    def __init__(self, fetch=None, read=None, run_preview=None, env=os.environ, inventory=None):
        self.sessions: dict[str, dict] = {}
        self.fetch = fetch or hostkeys.fetch_keys
        self.reader = read or ssh_read.read_switch
        self.inventory = inventory or switch_inventory.read_at
        self.run_preview = run_preview or _subprocess_preview
        self.env = env

    # Read when used: the login may be changed from the GUI's settings.
    @property
    def login(self) -> str:
        return self.env.get("COLLECTOR_LOGIN") or self.env.get("GNETCLI_DEV_LOGIN") or ""

    @property
    def password(self) -> str:
        env = self.env
        return (env.get("COLLECTOR_PASSWORD") if env.get("COLLECTOR_LOGIN") else env.get("GNETCLI_DEV_PASSWORD")) or ""

    def _session(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SegmentError(f"no import session {session_id!r} (sessions end when the orchestrator restarts)")
        return session

    @staticmethod
    def view(session_id: str, session: dict) -> dict:
        """The session for the GUI: keys with fingerprints, what was read; never an output."""
        switches = []
        for address, s in session["switches"].items():
            switches.append({
                "address": address,
                "offered": {str(p): [hostkeys.key_info(k) for k in keys] for p, keys in s["offered"].items()},
                "same_on_both_ports": 830 not in s["offered"] or set(s["offered"].get(22, [])) == set(s["offered"][830]),
                "approved": s["pins"] is not None, "approved_by": s.get("approved_by"),
                "sysname": s.get("sysname"), "read": s.get("outputs") is not None, "error": s.get("error"),
                "in_netbox": s.get("in_netbox", False),
                "detected": _detected(s["found"]) if s.get("found") else None,
            })
        return {"id": session_id, "switches": switches}

    async def _offered(self, host: str) -> dict[int, list[str]]:
        """SSH's keys, and NETCONF's when it answers: it may be off on a switch being imported."""
        offered = {hostkeys.SSH_PORT: sorted((await asyncio.to_thread(self.fetch, host, hostkeys.SSH_PORT)).keys)}
        try:
            offered[hostkeys.NETCONF_PORT] = sorted(
                (await asyncio.to_thread(self.fetch, host, hostkeys.NETCONF_PORT)).keys)
        except hostkeys.HostKeyError:
            pass
        return offered

    async def start(self, netbox, addresses: list[str]) -> dict:
        """A session for these management addresses; fetches what each switch offers."""
        if not addresses or len(addresses) > MAX_SWITCHES:
            raise SegmentError(f"give 1-{MAX_SWITCHES} management addresses")
        seen, known = set(), set()
        for address in addresses:
            try:
                iface = ipaddress.IPv4Interface(address)
            except ValueError as exc:
                raise SegmentError(f"{address!r}: {exc}") from exc
            if "/" not in address or iface.network.prefixlen >= 31:
                raise SegmentError(f"{address}: give the management address with its mask, e.g. 172.24.0.104/24")
            if iface.ip in seen:
                raise SegmentError(f"{iface.ip} is given twice")
            seen.add(iface.ip)
            for ip in await netbox.find("ipam/ip-addresses", address=str(iface.ip)):
                if ip.get("vrf") is None and ip.get("assigned_object_id") is not None:
                    known.add(address)   # previewed, never written (write refuses it)
        switches = {}
        for address in addresses:
            host = address.split("/")[0]
            try:
                offered = await self._offered(host)
                hostkeys.plan_pin(offered, [], ports=offered)
                switches[address] = {"offered": offered, "pins": None}
            except hostkeys.HostKeyError as exc:
                switches[address] = {"offered": {}, "pins": None, "error": str(exc)}
            switches[address]["in_netbox"] = address in known
        session_id = uuid.uuid4().hex
        self.sessions[session_id] = {"switches": switches}
        return self.view(session_id, self.sessions[session_id])

    async def approve(self, session_id: str, address: str, keys: list[str], user: str) -> dict:
        """An approver's comparison: the switch must still offer exactly these keys."""
        session = self._session(session_id)
        switch = session["switches"].get(address)
        if switch is None:
            raise SegmentError(f"{address} is not in this import")
        given = sorted({hostkeys.parse_key(k) for k in keys})
        if not given:
            raise hostkeys.HostKeyError("no keys approved")
        host = address.split("/")[0]
        fetched = await self._offered(host)
        pins = hostkeys.plan_pin(fetched, given, ports=fetched)   # refuses keys offered but not approved
        if sorted({k for v in pins.values() for k in v}) != given:
            raise hostkeys.HostKeyError("the switch no longer offers the keys you approved; start again")
        switch.update(pins=pins, approved_by=user, error=None)
        return self.view(session_id, session)

    async def read(self, session_id: str) -> dict:
        """Read every approved switch (one at a time: VRP resets connections that come too fast)."""
        session = self._session(session_id)
        if not self.login:
            raise SegmentError("no read account (COLLECTOR_LOGIN or GNETCLI_DEV_LOGIN) is set")
        for address, switch in session["switches"].items():
            if switch["pins"] is None:
                continue
            try:
                sysname, outputs = await asyncio.to_thread(
                    self.reader, address.split("/")[0], switch["pins"], self.login,
                    self.password, COMMANDS)
                switch.update(sysname=sysname, error=None,
                              outputs=dict(zip(("version", "lldp", "config"), outputs)))
            except (ssh_read.SshReadError, OSError) as exc:
                switch.update(outputs=None, error=f"cannot read: {exc}")
        return self.view(session_id, session)

    async def detect(self, session_id: str) -> dict:
        """New fabric and Add switches: each approved switch's model and ports, read over NETCONF
        (`switch_inventory.read_at`) before it is placed, so any model can be built with its real ports."""
        session = self._session(session_id)
        if not self.login:
            raise SegmentError("no read account (COLLECTOR_LOGIN or GNETCLI_DEV_LOGIN) is set")
        for address, switch in session["switches"].items():
            if switch["pins"] is None:
                continue
            if not switch["pins"].get(hostkeys.NETCONF_PORT):
                switch.update(found=None, error="NETCONF (port 830) does not answer: a switch's model and ports "
                                                "are read over NETCONF only")
                continue
            try:
                found = await asyncio.to_thread(self.inventory, address.split("/")[0],
                                                switch["pins"][hostkeys.NETCONF_PORT], self.login, self.password)
                switch.update(found=found, error=None)
            except SegmentError as exc:
                switch.update(found=None, error=str(exc))
        return self.view(session_id, session)

    def detected(self, session_id: str) -> dict[str, dict]:
        """{management address: {"found": Found, "pins": {port: keys}}} of the switches detected."""
        session = self._session(session_id)
        return {a: {"found": s["found"], "pins": s["pins"]} for a, s in session["switches"].items() if s.get("found")}

    def _request(self, session: dict, choices: dict) -> dict:
        read = {a: s["outputs"] for a, s in session["switches"].items() if s.get("outputs") is not None}
        missing = [a for a, s in session["switches"].items() if s.get("outputs") is None]
        if missing:
            raise SegmentError(f"not read yet: {', '.join(missing)} (approve their keys, read again, "
                               "or start without them)")
        return {**choices, "switches": read}

    async def preview(self, session_id: str, choices: dict) -> dict:
        from orchestrator.redact import redact

        request = self._request(self._session(session_id), choices)
        result = await self.run_preview(request)
        return json.loads(redact(json.dumps(result)))

    async def write(self, netbox, session_id: str, choices: dict) -> dict:
        """Write the fabric, then pin each read switch's approved keys. The session ends."""
        session = self._session(session_id)
        request = self._request(session, choices)
        if known := [a for a, s in session["switches"].items() if s.get("in_netbox")]:
            raise SegmentError(f"already a management address in NetBox: {', '.join(known)} (preview only)")
        switches, configs = {}, {}
        try:
            for address, outputs in sorted(request["switches"].items()):
                switch = parse_switch(outputs["config"], outputs.get("version", ""), outputs.get("lldp", ""))
                if switch.name in switches:
                    raise ImportError_(f"{address} and {switches[switch.name][1]} both have sysname {switch.name}")
                switches[switch.name] = (switch, address)
                configs[switch.name] = outputs["config"]
            plan = assemble(switches, choices.get("roles"), choices.get("architecture"), choices.get("gateway_pair"),
                            choices.get("drop"))
        except (ImportParseError, ImportError_, ValueError) as exc:
            raise SegmentError(str(exc)) from exc
        try:
            # Everything is built in memory first: NetBox has no transactions, and a fabric half
            # written cannot be imported again (its site exists).
            from orchestrator.memory_netbox import FakeNetBox

            await import_build.build(FakeNetBox(), assemble(switches, choices.get("roles"), choices.get("architecture"),
                                                            choices.get("gateway_pair"), choices.get("drop")),
                                     choices["name"], choices.get("pools"), choices.get("gateway_mac"), configs=configs)
            result = await import_build.build(netbox, plan, choices["name"], choices.get("pools"),
                                              choices.get("gateway_mac"), configs=configs)
        except ImportError_ as exc:
            raise SegmentError(str(exc)) from exc
        for name, (_, address) in switches.items():
            pins = session["switches"][address]["pins"]
            device = await netbox.find_one("dcim/devices", name=name)
            await netbox.update("dcim/devices", device["id"], {"custom_fields": {
                hostkeys.FIELD: {str(port): keys for port, keys in pins.items()}}})
        del self.sessions[session_id]
        return {**result, "misfits": plan.misfits}

    def drop(self, session_id: str) -> None:
        self._session(session_id)
        del self.sessions[session_id]


def _detected(found: switch_inventory.Found) -> dict:
    return {"model": found.model, "version": found.version, "patch": found.patch, "serial": found.serial,
            "ports": found.ports}


async def _subprocess_preview(request: dict, module: str = "orchestrator.import_preview") -> dict:
    """The preview in its own process (Annet's providers are set once per process); the request
    (running configs) goes through the pipe only."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", module,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), PREVIEW_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        raise SegmentError(f"the preview took more than {PREVIEW_TIMEOUT} s")
    if process.returncode != 0:
        from orchestrator.redact import redact

        raise SegmentError("the preview failed: " + redact(err.decode(errors="replace").strip().splitlines()[-1]
                                                            if err.strip() else f"exit {process.returncode}"))
    return json.loads(out)
