"""External sign-in: AD then RADIUS, mapped to roles and per-fabric limits; local accounts
always checked locally only."""

import asyncio
import json

import pytest

from orchestrator import db
from orchestrator.auth import Accounts
from orchestrator.directory import (NO_ACCESS, REFUSED, UNAVAILABLE, UNKNOWN, Directory, check_groups, from_groups,
                                    parse_radius)


def run(coro):
    return asyncio.run(coro)


OPS_DN = "CN=NetOps,OU=Groups,DC=example,DC=net"
NOC_DN = "CN=NOC,OU=Groups,DC=example,DC=net"
MAPPING = [{"group": OPS_DN, "role": "operator", "fabrics": {"example": True}},
           {"group": NOC_DN, "role": "viewer", "fabrics": {"east": False}}]


class FakeLdap:
    """LdapOps' exchanges against a dict of users: name -> (password, groups)."""

    def __init__(self, users, down=False):
        self.users, self.down, self.binds = users, down, []

    def service(self):
        if self.down:
            raise ConnectionError("ldaps://east.example.net:636 unreachable")
        return "service"

    def find_user(self, service, username):
        return [f"CN={username},OU=People,DC=example,DC=net"] if username in self.users else []

    def password_ok(self, dn, password):
        self.binds.append(dn)
        return self.users[dn.split(",")[0][3:]][0] == password

    def groups(self, service, dn):
        return self.users[dn.split(",")[0][3:]][1]


def directory(ldap=None, radius=None) -> Directory:
    env = {"DIRECTORY_LDAP_GROUPS": json.dumps(MAPPING)}
    return Directory(env, ldap_ops=ldap, radius_send=radius)


def test_group_mapping_rules():
    assert json.loads(check_groups(json.dumps([{"group": OPS_DN, "role": "approver", "fabrics": {"example": True}}]))) == [
        {"group": OPS_DN, "role": "approver", "fabrics": None}]          # approvers reach every fabric
    with pytest.raises(ValueError, match="role"):
        check_groups(json.dumps([{"group": OPS_DN, "role": "god"}]))
    # Highest role wins, fabrics add up, a viewer never approves.
    assert from_groups([OPS_DN.upper(), NOC_DN], MAPPING) == ("operator", {"east": False, "example": True})
    assert from_groups([NOC_DN], MAPPING) == ("viewer", {"east": False})
    assert from_groups(["CN=Other,DC=example,DC=net"], MAPPING) is None


def test_radius_attribute():
    assert parse_radius("operator:example+,east") == ("operator", {"east": False, "example": True})
    assert parse_radius("approver") == ("approver", None)
    assert parse_radius("viewer:example+") == ("viewer", {"example": False})
    assert parse_radius("root:example") is None and parse_radius("operator:Bad Site") is None and parse_radius("") is None


def test_ad_first_then_radius_only_for_unknown_or_unreachable_users():
    ldap = FakeLdap({"alice": ("pw-alice", [OPS_DN]), "carol": ("pw-carol", ["CN=Other,DC=x"])})
    asked = []

    def radius(user, password):
        asked.append(user)
        return (password == "pw-bob", ["operator:east+"])

    d = directory(ldap, radius)
    granted = run(d.authenticate("alice", "pw-alice"))
    assert (granted.outcome, granted.source, granted.role, granted.fabrics) == ("granted", "ldap", "operator", {"example": True})
    assert run(d.authenticate("alice", "wrong")).outcome == REFUSED and asked == []     # AD's answer stands
    assert run(d.authenticate("carol", "pw-carol")).outcome == NO_ACCESS
    bob = run(d.authenticate("bob", "pw-bob"))
    assert (bob.source, bob.role, bob.fabrics) == ("radius", "operator", {"east": True}) and asked == ["bob"]
    assert run(d.authenticate("bob", "nope")).outcome == REFUSED
    down = directory(FakeLdap({}, down=True), radius)
    assert run(down.authenticate("bob", "pw-bob")).outcome == "granted"                 # AD down: RADIUS answers
    assert run(directory(FakeLdap({}, down=True)).authenticate("bob", "pw")).outcome == UNAVAILABLE
    assert run(directory(FakeLdap({})).authenticate("nobody", "pw")).outcome == UNKNOWN


def test_an_empty_password_never_reaches_the_directory():
    ldap = FakeLdap({"alice": ("", [OPS_DN])})
    assert run(directory(ldap).authenticate("alice", "")).outcome == REFUSED and ldap.binds == []


def test_sign_in_through_the_directory_keeps_a_record_and_local_accounts_stay_local(database_url):
    users = {"alice": ("pw-alice", [OPS_DN])}
    accounts = Accounts(db.make_sessionmaker(db.make_engine(database_url, pooled=False)), directory=directory(FakeLdap(users)))
    run(accounts.create_user("root", "root-password", "admin"))

    assert run(accounts.login("Alice", "pw-alice")) is not None                          # case folded
    alice = next(u for u in run(accounts.list_users()) if u["username"] == "alice")
    assert (alice["source"], alice["role"], alice["fabrics"]) == ("ldap", "operator", {"example": True})
    with pytest.raises(ValueError, match="comes from the directory"):
        run(accounts.set_role("alice", "admin"))

    users["alice"] = ("pw-alice", [NOC_DN])                                               # moved to another group
    run(accounts.login("alice", "pw-alice"))
    alice = next(u for u in run(accounts.list_users()) if u["username"] == "alice")
    assert (alice["role"], alice["fabrics"]) == ("viewer", {"east": False})

    users["alice"] = ("pw-alice", [])                                                     # removed from every group
    assert run(accounts.login("alice", "pw-alice")) is None
    assert next(u for u in run(accounts.list_users()) if u["username"] == "alice")["active"] is False

    users["root"] = ("ad-root", [OPS_DN])                                                 # a local name is never the directory's
    assert run(accounts.login("root", "ad-root")) is None
    assert run(accounts.login("root", "root-password")) is not None
