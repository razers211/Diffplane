"""Manage platform users from the command line (inside the orchestrator container).

    python -m orchestrator.users create alice --role operator   # prompts for the password
    python -m orchestrator.users set-password alice
    python -m orchestrator.users set-role alice approver

Roles: viewer (read only) < operator (intent, diffs, deploy requests) < approver (also approves)
< admin (also accounts, settings, fabric deletion).
"""

import argparse
import asyncio
import getpass
import sys

from orchestrator import db
from orchestrator.auth import ROLES, Accounts
from orchestrator.config import Settings
from orchestrator.migrate import upgrade


async def run(action: str, username: str, password: str | None = None, role: str = "viewer") -> None:
    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")
    await asyncio.to_thread(upgrade, settings.database_url)
    engine = db.make_engine(settings.database_url)
    accounts = Accounts(db.make_sessionmaker(engine))
    try:
        if action == "create":
            await accounts.create_user(username, password, role)
        elif action == "set-role":
            await accounts.set_role(username, role)
        else:
            await accounts.set_password(username, password)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.users")
    parser.add_argument("action", choices=["create", "set-password", "set-role"])
    parser.add_argument("username")
    parser.add_argument("role_arg", nargs="?", choices=ROLES, metavar="role", help="for set-role")
    parser.add_argument("--role", choices=ROLES, default="viewer", help="for create (default: viewer)")
    args = parser.parse_args()
    password = None
    if args.action == "set-role":
        if args.role_arg is None:
            parser.error("set-role needs a role: " + ", ".join(ROLES))
    else:
        password = getpass.getpass(f"Password for {args.username}: ")
        if password != getpass.getpass("Again: "):
            print("passwords differ", file=sys.stderr)
            return 1
    try:
        asyncio.run(run(args.action, args.username, password, args.role_arg or args.role))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.action}: {args.username} done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
