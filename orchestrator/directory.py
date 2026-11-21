"""External sign-in: Active Directory over LDAPS, then RADIUS.

A local account is always checked locally only (auth.Accounts.login); any other name is asked of
the directory. AD first: a service account finds the user (escaped filter), the user's own
password is checked by binding as the user (an empty password is refused before any bind: an
LDAP bind without a password succeeds anonymously), the user's groups come from a group filter
(AD's nested-group search by default) and map to a role and per-fabric limits. RADIUS only when
AD does not know the user or cannot be reached: PAP, the role and fabrics read from one attribute
(`role:fabric,fabric`, a `+` after a fabric: may approve). No mapped group or attribute: refused.

Settings (Admin › Directory) live in the platform settings store as DIRECTORY_* keys.
"""

import asyncio
import json
import os
import ssl
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.auth import LIMITABLE, ROLES, SITE, USERNAME

DEFAULT_USER_FILTER = "(sAMAccountName={username})"
DEFAULT_GROUP_FILTER = "(member:1.2.840.113556.1.4.1941:={user_dn})"   # AD: nested groups
DEFAULT_RADIUS_ATTRIBUTE = "Filter-Id"
RADIUS_DICTIONARY = Path(__file__).with_name("radius.dict")
TIMEOUT = 5

GRANTED, UNKNOWN, REFUSED, NO_ACCESS, UNAVAILABLE = "granted", "unknown", "refused", "no-access", "unavailable"


@dataclass
class Result:
    outcome: str                         # granted, unknown, refused, no-access, unavailable
    source: str | None = None            # ldap or radius
    role: str | None = None
    fabrics: dict | None = None          # None: every fabric
    detail: str = ""
    groups: list[str] = field(default_factory=list)


# -- mapping ----------------------------------------------------------------------------------------

def _clean_fabrics(role: str, fabrics) -> dict | None:
    if fabrics is None or role not in LIMITABLE:
        return None
    if not isinstance(fabrics, dict) or not fabrics or not all(isinstance(s, str) and SITE.fullmatch(s) for s in fabrics):
        raise ValueError("fabrics: null (every fabric) or {fabric: may approve}")
    return {site: bool(approve) and role == "operator" for site, approve in sorted(fabrics.items())}


def check_groups(text: str) -> str:
    """The group mapping, validated: [{"group": DN, "role": role, "fabrics": null | {site: may approve}}]."""
    try:
        rows = json.loads(text)
    except ValueError:
        raise ValueError("group mapping: JSON") from None
    if not isinstance(rows, list):
        raise ValueError("group mapping: a list of {group, role, fabrics}")
    clean = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("group"), str) or "=" not in row["group"]:
            raise ValueError("group mapping: each row needs a group DN")
        if row.get("role") not in ROLES:
            raise ValueError(f"group mapping: role is one of {', '.join(ROLES)}")
        clean.append({"group": row["group"].strip(), "role": row["role"], "fabrics": _clean_fabrics(row["role"], row.get("fabrics"))})
    return json.dumps(clean)


def grant(role_fabrics: list[tuple[str, dict | None]]) -> tuple[str, dict | None] | None:
    """Several matches: the highest role wins and fabrics add up (every fabric if any match has no limit)."""
    if not role_fabrics:
        return None
    role = max((r for r, _ in role_fabrics), key=ROLES.index)
    if role not in LIMITABLE or any(f is None for _, f in role_fabrics):
        return role, None
    fabrics: dict[str, bool] = {}
    for _, limits in role_fabrics:
        for site, approve in limits.items():
            fabrics[site] = fabrics.get(site, False) or approve
    return role, {s: a and role == "operator" for s, a in sorted(fabrics.items())}


def from_groups(groups: list[str], mapping: list[dict]) -> tuple[str, dict | None] | None:
    member = {g.strip().lower() for g in groups}
    return grant([(row["role"], row["fabrics"]) for row in mapping if row["group"].lower() in member])


def parse_radius(value: str | None) -> tuple[str, dict | None] | None:
    """`role` or `role:fabric,fabric+` -> (role, fabrics); None if it is not that."""
    if not value:
        return None
    role, _, rest = value.strip().partition(":")
    if role not in ROLES:
        return None
    if not rest.strip():
        return role, None
    fabrics = {}
    for item in (i.strip() for i in rest.split(",") if i.strip()):
        site, approve = (item[:-1], True) if item.endswith("+") else (item, False)
        if not SITE.fullmatch(site):
            return None
        fabrics[site] = fabrics.get(site, False) or approve
    return grant([(role, _clean_fabrics(role, fabrics) if fabrics else None)])


# -- LDAP (ldap3) -----------------------------------------------------------------------------------

class LdapOps:
    """The three LDAP exchanges, over LDAPS with the configured CA. Synchronous (run in a thread)."""

    def __init__(self, env):
        from ldap3 import FIRST, Server, ServerPool, Tls

        urls = [u for u in (env.get("DIRECTORY_LDAP_URLS") or "").split(",") if u]
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_data=env.get("DIRECTORY_LDAP_CA_CERT") or None)
        servers = []
        for url in urls:
            host, _, port = url.removeprefix("ldaps://").partition(":")
            servers.append(Server(host, port=int(port or 636), use_ssl=True, tls=tls, connect_timeout=TIMEOUT))
        self.pool = ServerPool(servers, FIRST, active=1, exhaust=True)
        self.env = env

    def _connection(self, user: str, password: str):
        from ldap3 import Connection

        return Connection(self.pool, user=user, password=password, auto_bind=True, receive_timeout=TIMEOUT,
                          raise_exceptions=True)

    def _search(self, connection, search_filter: str) -> list[str]:
        from ldap3 import SUBTREE

        connection.search(self.env["DIRECTORY_LDAP_BASE_DN"], search_filter, SUBTREE, attributes=[])
        return [r["dn"] for r in connection.response if r.get("type") == "searchResEntry"]

    def service(self):
        return self._connection(self.env["DIRECTORY_LDAP_BIND_DN"], self.env["DIRECTORY_LDAP_BIND_PASSWORD"])

    def find_user(self, service, username: str) -> list[str]:
        from ldap3.utils.conv import escape_filter_chars

        template = self.env.get("DIRECTORY_LDAP_USER_FILTER") or DEFAULT_USER_FILTER
        return self._search(service, template.replace("{username}", escape_filter_chars(username)))

    def password_ok(self, user_dn: str, password: str) -> bool:
        from ldap3.core.exceptions import LDAPBindError, LDAPInvalidCredentialsResult

        try:
            self._connection(user_dn, password).unbind()
            return True
        except (LDAPBindError, LDAPInvalidCredentialsResult):
            return False

    def groups(self, service, user_dn: str) -> list[str]:
        from ldap3.utils.conv import escape_filter_chars

        template = self.env.get("DIRECTORY_LDAP_GROUP_FILTER") or DEFAULT_GROUP_FILTER
        return self._search(service, template.replace("{user_dn}", escape_filter_chars(user_dn)))


def ldap_sign_in(ops, username: str, password: str, mapping: list[dict]) -> Result:
    """AD's answer for one sign-in (synchronous; `ops` is LdapOps or a test double)."""
    try:
        service = ops.service()
        found = ops.find_user(service, username)
        if not found:
            return Result(UNKNOWN, "ldap", detail="not in the directory")
        if len(found) > 1:
            return Result(REFUSED, "ldap", detail="the user filter matches more than one entry")
        if not ops.password_ok(found[0], password):
            return Result(REFUSED, "ldap", detail="wrong password")
        groups = ops.groups(service, found[0])
    except Exception as exc:   # unreachable, TLS, service account refused: RADIUS may still answer
        return Result(UNAVAILABLE, "ldap", detail=f"Active Directory: {type(exc).__name__}: {exc}"[:300])
    granted = from_groups(groups, mapping)
    if granted is None:
        return Result(NO_ACCESS, "ldap", detail="no mapped group", groups=groups)
    return Result(GRANTED, "ldap", role=granted[0], fabrics=granted[1], groups=groups)


# -- RADIUS (pyrad) ---------------------------------------------------------------------------------

def radius_exchange(env, username: str, password: str) -> tuple[bool, list[str]]:
    """PAP Access-Request to the configured servers in order; (accepted, values of the attribute)."""
    from pyrad import packet
    from pyrad.client import Client
    from pyrad.dictionary import Dictionary

    attribute = env.get("DIRECTORY_RADIUS_ATTRIBUTE") or DEFAULT_RADIUS_ATTRIBUTE
    dictionary = Dictionary(str(RADIUS_DICTIONARY))
    errors = []
    for server in [s for s in (env.get("DIRECTORY_RADIUS_SERVERS") or "").split(",") if s]:
        host, _, port = server.partition(":")
        client = Client(server=host, authport=int(port or 1812), secret=env["DIRECTORY_RADIUS_SECRET"].encode(),
                        dict=dictionary)
        client.timeout, client.retries = TIMEOUT, 1
        request = client.CreateAuthPacket(code=packet.AccessRequest, User_Name=username, NAS_Identifier="fabric-platform")
        request["User-Password"] = request.PwCrypt(password)
        try:
            reply = client.SendPacket(request)
        except Exception as exc:   # timeout or socket error: the next server
            errors.append(f"{server}: {type(exc).__name__}")
            continue
        values = [v.decode(errors="replace") if isinstance(v, bytes) else str(v) for v in reply.get(attribute, [])]
        return reply.code == packet.AccessAccept, values
    raise ConnectionError("no RADIUS server answered (" + ", ".join(errors) + ")")


def radius_sign_in(send, username: str, password: str) -> Result:
    try:
        accepted, values = send(username, password)
    except Exception as exc:
        return Result(UNAVAILABLE, "radius", detail=f"RADIUS: {exc}"[:300])
    if not accepted:
        return Result(REFUSED, "radius", detail="RADIUS refused")
    granted = grant([g for g in (parse_radius(v) for v in values) if g])
    if granted is None:
        return Result(NO_ACCESS, "radius", detail="no role in the RADIUS answer")
    return Result(GRANTED, "radius", role=granted[0], fabrics=granted[1])


# -- the sign-in order ------------------------------------------------------------------------------

class Directory:
    def __init__(self, env=os.environ, ldap_ops=None, radius_send=None):
        self.env = env
        self._ldap_ops = ldap_ops            # tests: a double with LdapOps' methods
        self._radius_send = radius_send      # tests: (username, password) -> (accepted, values)

    def providers(self) -> list[str]:
        e = self.env
        ldap = all(e.get(k) for k in ("DIRECTORY_LDAP_URLS", "DIRECTORY_LDAP_BIND_DN", "DIRECTORY_LDAP_BIND_PASSWORD",
                                       "DIRECTORY_LDAP_BASE_DN", "DIRECTORY_LDAP_CA_CERT")) or self._ldap_ops is not None
        radius = all(e.get(k) for k in ("DIRECTORY_RADIUS_SERVERS", "DIRECTORY_RADIUS_SECRET")) or self._radius_send is not None
        return [p for p, on in (("ldap", ldap), ("radius", radius)) if on]

    def mapping(self) -> list[dict]:
        return json.loads(self.env.get("DIRECTORY_LDAP_GROUPS") or "[]")

    async def authenticate(self, username: str, password: str) -> Result:
        """AD, then RADIUS when AD does not know the user or cannot be reached."""
        if not password:
            return Result(REFUSED, detail="empty password")   # never an LDAP bind without one
        if not USERNAME.fullmatch(username):
            return Result(REFUSED, detail="not a valid user name")
        unavailable = []
        for provider in self.providers():
            if provider == "ldap":
                ops = self._ldap_ops or LdapOps(self.env)
                result = await asyncio.to_thread(ldap_sign_in, ops, username, password, self.mapping())
            else:
                send = self._radius_send or (lambda u, p: radius_exchange(self.env, u, p))
                result = await asyncio.to_thread(radius_sign_in, send, username, password)
            if result.outcome == UNAVAILABLE:
                unavailable.append(result.detail)
            elif result.outcome != UNKNOWN:
                return result
        if unavailable:
            return Result(UNAVAILABLE, detail="; ".join(unavailable))
        return Result(UNKNOWN, detail="not known to any directory")
