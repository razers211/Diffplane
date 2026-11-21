"""Which fabrics an account reaches.

Admins and approvers reach every fabric. A viewer or operator may be limited to chosen fabrics
(`users.fabrics`, {site: may approve}); such an account sees nothing of the others. A limited
operator approves deploys of the fabrics where an admin ticked "may approve".
"""

from dataclasses import dataclass

from orchestrator.auth import LIMITABLE, role_allows


@dataclass(frozen=True)
class Access:
    role: str
    fabrics: dict | None = None     # None: every fabric

    @classmethod
    def of(cls, account) -> "Access":
        role = getattr(account, "role", "viewer")
        fabrics = getattr(account, "fabrics", None)
        return cls(role, dict(fabrics) if fabrics is not None and role in LIMITABLE else None)

    @property
    def limited(self) -> bool:
        return self.fabrics is not None

    def sees(self, site: str | None) -> bool:
        return not self.limited or (site is not None and site in self.fabrics)

    def may_approve(self, site: str | None) -> bool:
        if role_allows(self.role, "approver"):
            return True
        return self.role == "operator" and self.limited and bool(self.fabrics.get(site))
