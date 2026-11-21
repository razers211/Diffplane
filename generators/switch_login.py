import json
import os
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import fabric_settings

NAME = "fab-radius"   # the template and both schemes
VPN = "_management_vpn_"   # referenced only, never changed
AUTH_PORT, ACCT_PORT = 1812, 1813


class MissingRadiusKey(Exception):
    pass


def switch_login(device: Any) -> dict:
    """`fabric.switch_login`: `site`, `enabled`, `domain`, `servers` [addresses]. Kept with `enabled`
    false once set, so the removal is owned."""
    return ((device.config_context or {}).get("fabric") or {}).get("switch_login") or {}


def local_accounts(device: Any) -> dict:
    """`fabric.local_accounts`: `site`, `accounts` {name: level}, `owned` [every name it ever had]."""
    return ((device.config_context or {}).get("fabric") or {}).get("local_accounts") or {}


class SwitchLogin(PartialGenerator):
    """People's logins over RADIUS (amending "never manage the management
    plane" for exactly these items): a separate domain, so `user@<domain>` goes to RADIUS then local
    and the default domain, the platform's accounts, stays local-only. Accounting on, no command
    authorization; the privilege comes from the RADIUS answer.

    It also writes people's local accounts made from the GUI (`fabric.local_accounts`): owned by exact
    name, so hand-made accounts and the platform's own are never touched. The password is the initial
    one: VRP makes the user change it at first login, and the rule compares it by presence.

    These lines reach a switch only under `commit trial` (`DeployJobs._deploy_login`). The ACL names
    exactly the platform's objects: never `domain default`, a default scheme or a `local-user`.
    """

    TAGS = ["management"]

    def acl_huawei(self, device: Any) -> str:
        login, accounts = switch_login(device), local_accounts(device)
        if not login and not accounts:
            return ""
        acl = ["aaa %cant_delete=1"]
        if login:
            acl[:0] = [f"radius-server template {NAME}", "    ~ %global"]
            acl += [f"    authentication-scheme {NAME}", "        ~ %global",
                    f"    accounting-scheme {NAME}", "        ~ %global",
                    f"    domain {login['domain']}", "        ~ %global"]
        acl += [f"    local-user {name} ~" for name in accounts.get("owned", [])]
        return "\n".join(acl)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        login, accounts = switch_login(device), local_accounts(device)
        if not login.get("enabled") and not accounts.get("accounts"):
            return
        fabric_settings(device)   # fails on a missing fabric context, as every generator
        if login.get("enabled"):
            key = json.loads(os.environ.get("RADIUS_KEYS") or "{}").get(login["site"])
            if not key:
                raise MissingRadiusKey(f"the RADIUS key of {login['site']} is not in RADIUS_KEYS: "
                                       "only the orchestrator's runs have it")
            with self.block("radius-server template", NAME):
                yield "radius-server user-name domain-included"   # VRP adds it (a test switch)
                yield "radius-server shared-key cipher", key
                for server in login["servers"]:
                    yield "radius-server accounting", server, ACCT_PORT, "vpn-instance", VPN, "weight 80"
                    yield "radius-server authentication", server, AUTH_PORT, "vpn-instance", VPN, "weight 80"
        with self.block("aaa"):
            if login.get("enabled"):
                with self.block("authentication-scheme", NAME):
                    yield "authentication-mode radius local"
                with self.block("accounting-scheme", NAME):
                    yield "accounting-mode radius"
                with self.block("domain", login["domain"]):
                    yield "authentication-scheme", NAME
                    yield "accounting-scheme", NAME
                    yield "radius-server", NAME
            passwords = json.loads(os.environ.get("LOCAL_PASSWORDS") or "{}").get(accounts.get("site")) or {}
            for name, level in sorted((accounts.get("accounts") or {}).items()):
                if name not in passwords:
                    raise MissingRadiusKey(f"the password of {name} is not in LOCAL_PASSWORDS")
                yield f"local-user {name} password irreversible-cipher", passwords[name]
                yield f"local-user {name} privilege level", level
                yield f"local-user {name} service-type ssh"
