"""First-time setup: the first admin of a platform with no account.

While no account exists, the GUI offers a setup page instead of the login. Anyone who can reach the
page could claim the platform, so it asks for a one-time code that only someone who can read the
orchestrator's log has: printed at start, replaced (and printed again) after too many wrong tries.
Once any account exists, setup is refused for good.
"""

import hmac
import secrets
import sys

MAX_TRIES = 10


class FirstSetup:
    def __init__(self, out=sys.stderr):
        self.code: str | None = None
        self.tries = 0
        self.out = out

    def _new_code(self) -> None:
        raw = secrets.token_hex(4).upper()
        self.code = f"{raw[:4]}-{raw[4:]}"
        self.tries = 0
        print(f"SETUP: no account exists yet. Open the GUI and enter the setup code {self.code}", file=self.out, flush=True)

    async def start(self, accounts) -> None:
        """At start: a code if the platform has no account."""
        if accounts is not None and await accounts.count() == 0:
            self._new_code()

    async def needed(self, accounts) -> bool:
        return accounts is not None and await accounts.count() == 0

    async def claim(self, accounts, code: str, username: str, password: str) -> None:
        """Create the first admin. Raises ValueError (wrong code, setup over, bad account)."""
        if not await self.needed(accounts):
            self.code = None
            raise PermissionError("the platform is already set up: sign in instead")
        if self.code is None:
            self._new_code()
        if not hmac.compare_digest(code.strip().upper(), self.code):
            self.tries += 1
            if self.tries >= MAX_TRIES:
                self._new_code()   # too many tries: a fresh code, printed again
                raise ValueError("wrong setup code; a new code is now in the orchestrator's log")
            raise ValueError("wrong setup code (it is in the orchestrator's log)")
        await accounts.create_user(username, password, "admin", actor=username)
        self.code = None
