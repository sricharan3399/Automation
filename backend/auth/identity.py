"""Resolution of the operator identity used for audit and RBAC."""

from __future__ import annotations

from dataclasses import dataclass

from backend.auth.roles import Permission, Role, permissions_for
from backend.settings import get_settings


@dataclass(frozen=True)
class Identity:
    user: str
    role: Role

    @property
    def permissions(self) -> set[Permission]:
        return permissions_for(self.role)

    def to_dict(self) -> dict[str, object]:
        return {
            "user": self.user,
            "role": self.role.value,
            "permissions": sorted(p.value for p in self.permissions),
        }


def resolve_identity(header_user: str | None = None, header_role: str | None = None) -> Identity:
    """Resolve the acting identity.

    In a local desktop deployment the identity comes from configuration. When
    the app is placed behind an approved authenticating reverse proxy, that
    proxy's forwarded headers take precedence.
    """
    settings = get_settings()
    user = header_user or settings.local_user
    role = Role.parse(header_role or settings.local_role)
    return Identity(user=user, role=role)
