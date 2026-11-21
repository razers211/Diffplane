"""Read-only CLI for switches not (yet) in NetBox (import).

The importer reads a switch before it exists in NetBox: its address and approved host keys
are given directly. The pinned key is checked before logging in, and only the commands in
`ALLOWED` are ever sent; nothing is configured. Outputs hold secrets (`display
current-configuration`): callers keep them in memory only.

`read_switch` uses NETCONF when it answers and SSH when NETCONF is off.
The importer's parsers read the CLI text, which no YANG module gives, so NETCONF carries the same
`display` commands through huawei-cli `execute-command` (same text as SSH on a test switch).
"""

import logging
import re
import socket
import time
from xml.sax.saxutils import escape

from orchestrator import hostkeys
from orchestrator.routes import CLI, parse_cli
from orchestrator.segments import SegmentError

log = logging.getLogger(__name__)

ALLOWED = (
    "screen-length 0 temporary",          # no paging, for this session only
    "display version",
    "display lldp neighbor brief",
    "display current-configuration",
)
SSH_PORT = 22

# VRP's user-view prompt: <sysname>
PROMPT = re.compile(r"(?:^|\n)\r?<([^<>\s]+)>\s*$")
MORE = "---- More ----"


class SshReadError(Exception):
    pass


class NetconfUnavailable(Exception):
    """NETCONF does not answer here: the import reads over SSH instead. Never a wrong host key."""


def check_commands(commands: list[str]) -> None:
    for command in commands:
        if command not in ALLOWED:
            raise ValueError(f"not an allowed read-only command: {command!r}")


def clean(raw: str, command: str) -> str:
    """One command's output: without the echoed command, the prompt and terminal noise."""
    text = raw.replace("\r\n", "\n").replace("\r", "")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)            # ANSI sequences
    text = re.sub(r"\s*" + re.escape(MORE) + r"\s*", "\n", text)
    text = PROMPT.sub("", text)
    lines = text.split("\n")
    if lines and lines[0].strip() == command:
        lines = lines[1:]
    return "\n".join(lines).strip("\n") + "\n"


class Shell:
    """One interactive session: send a command, read until the prompt comes back."""

    def __init__(self, channel, timeout: float):
        self.channel = channel
        self.timeout = timeout

    def _read_until_prompt(self) -> str:
        buffer, deadline = "", time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.channel.recv_ready():
                chunk = self.channel.recv(65536).decode("utf-8", errors="replace")
                buffer += chunk
                if buffer.rstrip().endswith(MORE):     # paging despite screen-length: next page
                    self.channel.send(" ")
                    continue
                if PROMPT.search(buffer):
                    return buffer
            elif self.channel.exit_status_ready():
                raise SshReadError("the switch closed the session")
            else:
                time.sleep(0.05)
        raise SshReadError(f"no prompt within {self.timeout:.0f} s")

    def start(self) -> str:
        """Wait for the first prompt; returns the sysname it shows."""
        return PROMPT.search(self._read_until_prompt()).group(1)

    def run(self, command: str) -> str:
        check_commands([command])
        self.channel.send(command + "\n")
        return clean(self._read_until_prompt(), command)


def read(host: str, keys: list[str], login: str, password: str, commands: list[str],
         timeout: float = 60.0) -> tuple[str, list[str]]:
    """(sysname, outputs) of `commands` on the switch at `host`, whose SSH host key must be one
    of `keys` ("<type> <base64>")."""
    check_commands(commands)
    if not keys:
        raise SshReadError(f"{host}: no approved host key")
    import paramiko

    sock = socket.create_connection((host, SSH_PORT), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        if f"{key.get_name()} {key.get_base64()}" not in keys:
            raise SshReadError(f"{host}: the switch presents another host key than the approved one")
        transport.auth_password(login, password)
        channel = transport.open_session(timeout=timeout)
        channel.get_pty(width=512, height=0)
        channel.invoke_shell()
        shell = Shell(channel, timeout)
        sysname = shell.start()
        shell.run("screen-length 0 temporary")
        return sysname, [shell.run(c) for c in commands]
    except paramiko.SSHException as exc:
        raise SshReadError(f"{host}: {type(exc).__name__}: {exc}") from exc
    finally:
        transport.close()


def _rpc(commands: list[str]) -> str:
    check_commands(commands)
    cmds = "".join(f"<cli-cmd><sequence>{i}</sequence><command>{escape(c)}</command></cli-cmd>"
                   for i, c in enumerate(commands, 1))
    return f'<execute-command xmlns="{CLI}"><cli-cmds>{cmds}</cli-cmds></execute-command>'


def netconf_read(host: str, keys: list[str], login: str, password: str, commands: list[str],
                 timeout: float = 60.0) -> tuple[str, list[str]]:
    """As `read`, over NETCONF; `keys` are the approved keys of port 830."""
    from lxml import etree
    from ncclient import manager
    from ncclient import NCClientError
    from ncclient.transport.errors import SSHUnknownHostError

    allowed = {hostkeys.md5_fingerprint(key) for key in keys}
    try:
        with manager.connect(host=host, port=hostkeys.NETCONF_PORT, username=login, password=password,
                             hostkey_verify=True, unknown_host_cb=lambda _, fingerprint: fingerprint in allowed,
                             allow_agent=False, look_for_keys=False, device_params={"name": "default"},
                             timeout=timeout) as session:
            outputs = parse_cli(session.dispatch(etree.fromstring(_rpc(commands))).xml, len(commands))
    except SSHUnknownHostError as exc:
        raise SshReadError(f"{host}: the switch presents another NETCONF host key than the approved one") from exc
    except (NCClientError, OSError, SegmentError) as exc:
        raise NetconfUnavailable(f"{type(exc).__name__}: {exc}") from exc
    config = next((o for c, o in zip(commands, outputs) if c == "display current-configuration"), "")
    sysname = re.search(r"^sysname (\S+)", config, re.MULTILINE)
    return (sysname.group(1) if sysname else host), outputs


def read_switch(host: str, pins: dict[int, list[str]], login: str, password: str,
                commands: list[str]) -> tuple[str, list[str]]:
    """Over NETCONF where the switch has an approved key for it, else (or when it fails) SSH."""
    if pins.get(hostkeys.NETCONF_PORT):
        try:
            return netconf_read(host, pins[hostkeys.NETCONF_PORT], login, password, commands)
        except NetconfUnavailable as exc:
            log.info("import: %s reads over SSH: %s", host, exc)
    return read(host, pins[hostkeys.SSH_PORT], login, password, commands)
