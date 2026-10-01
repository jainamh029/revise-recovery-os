"""Role-based AUTHORIZATION scaffolding -- not authentication.

The role comes from an `X-Role` request header, which is trivially spoofable. It exists so the permission matrix is
defined, enforced server-side and tested before a real identity provider (Clerk / Supabase / SSO) is wired in.
With no header the request is treated as DEFAULT_ROLE (`approver` for the demo).
"""

from fastapi import Header

from .config import settings
from .errors import DomainError

ALL = {"viewer", "operations", "business_development", "finance", "approver", "admin"}
PERMS: dict[str, set[str]] = {
    "ops_write": {"operations", "approver", "admin"},
    "draft": {"business_development", "finance", "approver", "admin"},
    "finance_write": {"finance", "approver", "admin"},
    "commercial": {"finance", "operations", "approver", "admin"},  # listings, sales
    "approve": {"approver", "admin"},  # approve / decline / override
    "close": {"approver", "admin"},
    "config": {"approver", "admin"},
    "act": ALL - {"viewer"},  # alert actions
}


class Forbidden(DomainError):
    status_code = 403


def current_role(x_role: str | None = Header(default=None)) -> str:
    role = (x_role or settings.default_role).strip().lower()
    if role not in ALL:
        raise Forbidden(f"Unknown role '{role}'.", code="unknown_role")
    return role


def require(perm: str):
    from fastapi import Depends

    def _dep(role: str = Depends(current_role)) -> str:
        if role not in PERMS[perm]:
            raise Forbidden(
                f"Role '{role}' is not permitted to perform this action ({perm}).", code="forbidden"
            )
        return role

    return _dep
