"""Platform settings from the GUI.

A setting's value is the one an admin set in the GUI (platform database, table `settings`), else
its `.env` value as the orchestrator started with it. The effective value is written into this
process's environment, which every reader already uses (Annet runs and the generators get it per
run, redaction, ZTP, the route view and imports), so it applies without a restart; the few read
only at start say so (`applies="next start"`). The collector, another container, reads its login
from a file written here into the shared volume (`PLATFORM_SHARED`), at each reconnect.

Secrets are write-only: the API says where they come from, never what they are. Infrastructure
values (NetBox token, database passwords...) are not editable here: a wrong one would lock the
platform out of its own databases.
"""

import ipaddress
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import delete, select

from orchestrator.auth import audit
from orchestrator.db import Setting, now


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    group: str
    secret: bool = False
    kind: str = "text"          # text, login, int, interface, network, address, hostnames
    help: str = ""
    applies: str = "now"        # now | next start
    low: int | None = None
    high: int | None = None
    hidden: bool = False        # set elsewhere in the GUI (the Fabric page), not on the Settings page
    choices: tuple[str, ...] = ()


CATALOGUE: list[Spec] = [
    Spec("GNETCLI_DEV_LOGIN", "Platform login on the switches", "Switch access", kind="login",
         help="The account Annet uses to read and change switch config (SSH)."),
    Spec("GNETCLI_DEV_PASSWORD", "Its password", "Switch access", secret=True),
    Spec("COLLECTOR_LOGIN", "Read-only login (collector)", "Switch access", kind="login",
         help="The account the collector, the route view and imports use to read switches (NETCONF, SSH)."),
    Spec("COLLECTOR_PASSWORD", "Its password", "Switch access", secret=True),
    Spec("SNMP_COMMUNITY", "SNMP read community", "Switch access", secret=True,
         help="Configured on every switch; changing it shows as a change on every switch at the next deploy."),
    Spec("MLAG_DFS_PASSWORD", "M-LAG DFS group key", "Switch access", secret=True,
         help="Shared by the two leaves of each M-LAG pair."),
    Spec("ZTP_ADMIN_LOGIN", "Admin account for people (new switches)", "Zero-touch provisioning", kind="login",
         help="Created on each switch by its first-boot config."),
    Spec("ZTP_ADMIN_PASSWORD", "Its password", "Zero-touch provisioning", secret=True),
    Spec("ZTP_DHCP_INTERFACE", "DHCP interface", "Zero-touch provisioning", kind="interface",
         help="The host interface on the switches' management network. Empty: DHCP stays off."),
    Spec("ZTP_DHCP_NETWORK", "Management network", "Zero-touch provisioning", kind="network"),
    Spec("ZTP_SERVER_ADDRESS", "This server's address on it", "Zero-touch provisioning", kind="address",
         help="Where new switches download their first-boot config."),
    Spec("SFTP_ADDRESS", "Image server address", "Switch software", kind="address",
         help="Where the switches reach this platform's image server over MEth (the southbound VIP in HA). "
              "Every switch is given the server's key for it at its next deploy. Empty: upgrades stay off."),
    Spec("DRIFT_INTERVAL_SECONDS", "Check switches against intent every (seconds)", "Platform", kind="int", low=60, high=86400),
    Spec("SESSION_TTL_HOURS", "Sign-in lasts (hours)", "Platform", kind="int", low=1, high=720),
    Spec("TLS_HOSTNAMES", "Names and addresses in the HTTPS certificate", "Platform", kind="hostnames",
         applies="next start", help="Comma-separated. A new certificate is made at the next start if a name is missing."),
    Spec("DEPLOY_ALLOWED_SITES", "Fabrics deploys may go to", "Platform", kind="sites", hidden=True),
    Spec("CHANGE_WINDOWS", "Usual change windows", "Platform", kind="windows", hidden=True),   # per fabric
    Spec("ALERT_RECEIVER", "Send alerts to", "Alerts", kind="choice", choices=("none", "email", "webhook"),
         help="none: alerts are kept in Alertmanager only. Send a test alert to check the receiver."),
    Spec("ALERT_EMAIL_TO", "Recipients", "Alerts", kind="emails", help="Comma-separated addresses."),
    Spec("ALERT_EMAIL_FROM", "Sender address", "Alerts", kind="emails"),
    Spec("ALERT_SMTP_HOST", "SMTP server", "Alerts", kind="hostport", help="host:port, e.g. smtp.example.net:587 (TLS required)."),
    Spec("ALERT_SMTP_USER", "SMTP login (optional)", "Alerts", kind="login"),
    Spec("ALERT_SMTP_PASSWORD", "SMTP password", "Alerts", secret=True),
    Spec("ALERT_WEBHOOK_URL", "Webhook URL", "Alerts", secret=True, kind="url",
         help="Alertmanager's webhook format (JSON POST). Kept secret: such URLs often carry a token."),
    # External sign-in: edited on Admin › Directory, not on the Settings page.
    Spec("DIRECTORY_LDAP_URLS", "Active Directory servers", "Directory", kind="ldapurls", hidden=True,
         help="ldaps://host[:port], comma-separated, tried in order."),
    Spec("DIRECTORY_LDAP_BIND_DN", "Service account (DN)", "Directory", kind="dn", hidden=True),
    Spec("DIRECTORY_LDAP_BIND_PASSWORD", "Service account password", "Directory", secret=True, hidden=True),
    Spec("DIRECTORY_LDAP_BASE_DN", "Search base (DN)", "Directory", kind="dn", hidden=True),
    Spec("DIRECTORY_LDAP_USER_FILTER", "User filter", "Directory", kind="filter", hidden=True,
         help="Must contain {username}. Default (sAMAccountName={username})."),
    Spec("DIRECTORY_LDAP_GROUP_FILTER", "Group filter", "Directory", kind="filter", hidden=True,
         help="Must contain {user_dn}. Default: AD's nested groups, (member:1.2.840.113556.1.4.1941:={user_dn})."),
    Spec("DIRECTORY_LDAP_CA_CERT", "CA certificate (PEM)", "Directory", kind="pem", hidden=True),
    Spec("DIRECTORY_LDAP_GROUPS", "Group mapping", "Directory", kind="groups", hidden=True),
    Spec("DIRECTORY_RADIUS_SERVERS", "RADIUS servers", "Directory", kind="hostports", hidden=True,
         help="host[:port], comma-separated, tried in order; port 1812 by default."),
    Spec("DIRECTORY_RADIUS_SECRET", "RADIUS shared secret", "Directory", secret=True, hidden=True),
    Spec("DIRECTORY_RADIUS_ATTRIBUTE", "Attribute with role and fabrics", "Directory", kind="choice", hidden=True,
         choices=("Filter-Id", "Class", "Reply-Message"), help="Its value: role:fabric,fabric (a + after a fabric: may approve)."),
]
BY_KEY = {s.key: s for s in CATALOGUE}

# Shown read only: set in deploy/.env, changed there (with care) and applied by a restart.
FIXED = ["NETBOX_TOKEN", "NETBOX_SECRET_KEY", "NETBOX_DB_PASSWORD", "NETBOX_SUPERUSER_PASSWORD", "REDIS_PASSWORD",
         "PLATFORM_DB_PASSWORD", "CONFIG_BACKUP_KEY", "ORCHESTRATOR_BIND", "ORCHESTRATOR_PORT", "COOKIE_SECURE", "METRICS_TOKEN"]

# The .env values the orchestrator started with: "back to the .env value" restores these.
_STARTED = {s.key: os.environ.get(s.key) for s in CATALOGUE}

LOGIN = re.compile(r"[A-Za-z0-9._@-]{1,64}")
HOSTNAME = re.compile(r"[A-Za-z0-9.-]{1,253}")
INTERFACE = re.compile(r"[A-Za-z0-9._-]{1,32}")
SLUG = re.compile(r"[a-z0-9-]{1,100}")
EMAIL = re.compile(r"[^@\s,]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}")
HOSTPORT = re.compile(r"[A-Za-z0-9.-]{1,253}:\d{1,5}")
URL = re.compile(r"https?://\S{1,2040}")
LDAP_URL = re.compile(r"ldaps://[A-Za-z0-9.-]{1,253}(:\d{1,5})?")
HOSTPORT_OPT = re.compile(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?")


def check(spec: Spec, value: str) -> str:
    """The value cleaned, or ValueError with what is wrong."""
    value = value.strip()
    if spec.kind == "url":
        if not URL.fullmatch(value):
            raise ValueError(f"{spec.label}: an http(s) URL, up to 2048 characters, no spaces")
        return value
    if spec.secret:
        if len(value) < 6 or len(value) > 128 or any(c.isspace() for c in value):
            raise ValueError(f"{spec.label}: 6-128 characters, no spaces")
        return value
    if spec.kind == "login" and not LOGIN.fullmatch(value):
        raise ValueError(f"{spec.label}: 1-64 letters, digits, '.', '_', '@' or '-'")
    if spec.kind == "int":
        if not value.isdigit() or not spec.low <= int(value) <= spec.high:
            raise ValueError(f"{spec.label}: a whole number from {spec.low} to {spec.high}")
    if spec.kind == "interface" and value and not INTERFACE.fullmatch(value):
        raise ValueError(f"{spec.label}: an interface name such as eth1")
    if spec.kind == "network" and value:
        try:
            value = str(ipaddress.IPv4Network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"{spec.label}: {exc}") from exc
    if spec.kind == "address" and value:
        try:
            value = str(ipaddress.IPv4Address(value))
        except ValueError as exc:
            raise ValueError(f"{spec.label}: {exc}") from exc
    if spec.kind == "choice" and value not in spec.choices:
        raise ValueError(f"{spec.label}: one of {', '.join(spec.choices)}")
    if spec.kind == "emails":
        addresses = [a.strip() for a in value.split(",") if a.strip()]
        if not addresses or not all(EMAIL.fullmatch(a) for a in addresses):
            raise ValueError(f"{spec.label}: email addresses separated by commas")
        return ", ".join(addresses)
    if spec.kind == "hostport" and not HOSTPORT.fullmatch(value):
        raise ValueError(f"{spec.label}: host:port, e.g. smtp.example.net:587")
    if spec.kind == "ldapurls":
        urls = [u.strip() for u in value.split(",") if u.strip()]
        if not urls or not all(LDAP_URL.fullmatch(u) for u in urls):
            raise ValueError(f"{spec.label}: ldaps://host[:port], comma-separated (LDAPS only)")
        return ",".join(urls)
    if spec.kind == "hostports":
        hosts = [h.strip() for h in value.split(",") if h.strip()]
        if not hosts or not all(HOSTPORT_OPT.fullmatch(h) for h in hosts):
            raise ValueError(f"{spec.label}: host[:port], comma-separated")
        return ",".join(hosts)
    if spec.kind == "dn" and (not value or len(value) > 1024 or "=" not in value or "\n" in value):
        raise ValueError(f"{spec.label}: a distinguished name such as CN=fabric,OU=Services,DC=example,DC=net")
    if spec.kind == "filter":
        placeholder = "{username}" if "USER" in spec.key else "{user_dn}"
        if not (value.startswith("(") and value.endswith(")") and placeholder in value and len(value) <= 1024):
            raise ValueError(f"{spec.label}: an LDAP filter in parentheses containing {placeholder}")
    if spec.kind == "pem":
        if "-----BEGIN CERTIFICATE-----" not in value or len(value) > 65536:
            raise ValueError(f"{spec.label}: one or more PEM certificates (-----BEGIN CERTIFICATE-----)")
        return value
    if spec.kind == "groups":
        from orchestrator.directory import check_groups

        return check_groups(value)
    if spec.kind == "sites":
        slugs = sorted({n.strip() for n in value.split(",") if n.strip()})
        if not all(SLUG.fullmatch(n) for n in slugs):
            raise ValueError(f"{spec.label}: fabric names (site slugs) separated by commas")
        return ",".join(slugs)
    if spec.kind == "windows":
        from orchestrator.windows import check_all

        return check_all(value)
    if spec.kind == "hostnames":
        names = [n.strip() for n in value.split(",") if n.strip()]
        if not names or not all(HOSTNAME.fullmatch(n) for n in names):
            raise ValueError(f"{spec.label}: names or addresses separated by commas")
        value = ",".join(names)
    return value


class PlatformSettings:
    def __init__(self, sessions, shared: str | None = None, env=os.environ):
        self.sessions = sessions
        self.env = env
        self.shared = Path(shared or env.get("PLATFORM_SHARED") or "/nonexistent")
        self.gui: dict[str, str] = {}

    async def load(self) -> None:
        """At start and after every change: the GUI's values over the .env defaults."""
        async with self.sessions() as session:
            self.gui = {s.key: s.value for s in await session.scalars(select(Setting)) if s.key in BY_KEY}
        for spec in CATALOGUE:
            value = self.gui.get(spec.key, _STARTED.get(spec.key))
            if value is None:
                self.env.pop(spec.key, None)
            else:
                self.env[spec.key] = value
        self._share()

    def _share(self) -> None:
        """The collector's login, for the collector container (re-read at each reconnect)."""
        if not self.shared.is_dir():
            return
        path = self.shared / "collector.json"
        login, password = self.env.get("COLLECTOR_LOGIN"), self.env.get("COLLECTOR_PASSWORD")
        tmp = path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
            out.write(json.dumps({"login": login, "password": password}))
        tmp.replace(path)

    def rows(self, group: str | None = None) -> list[dict]:
        """Settings with where each value comes from; never a secret's value. `group`: one group,
        hidden ones included (a page of their own, e.g. Directory); None: the Settings page's."""
        rows = []
        for spec in (s for s in CATALOGUE if (s.group == group if group else not s.hidden)):
            source = "gui" if spec.key in self.gui else "env" if _STARTED.get(spec.key) else "unset"
            value = None if spec.secret else self.env.get(spec.key, "")
            rows.append({**asdict(spec), "source": source, "value": value})
        return rows

    def view(self) -> dict:
        """Every setting with where its value comes from; never a secret's value."""
        rows = self.rows()
        fixed = [{"key": k, "set": bool(os.environ.get(k)), "value": None if "PASSWORD" in k or "TOKEN" in k or "KEY" in k
                  else os.environ.get(k, "")} for k in FIXED]
        return {"settings": rows, "fixed": fixed}

    async def set(self, key: str, value: str, user: str) -> None:
        spec = BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"{key} is not a setting the GUI may change")
        value = check(spec, value)
        async with self.sessions() as session:
            row = await session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=value, updated_by=user, updated_at=now()))
            else:
                row.value, row.updated_by, row.updated_at = value, user, now()
            await session.commit()
        await audit(self.sessions, user, "settings.set", detail=key if spec.secret else f"{key}={value}")
        await self.load()

    async def reset(self, key: str, user: str) -> None:
        """Back to the .env value."""
        if key not in BY_KEY:
            raise ValueError(f"{key} is not a setting the GUI may change")
        async with self.sessions() as session:
            await session.execute(delete(Setting).where(Setting.key == key))
            await session.commit()
        await audit(self.sessions, user, "settings.reset", detail=key)
        await self.load()
