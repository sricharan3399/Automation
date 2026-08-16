"""Role-based access control.

The platform runs as a local desktop application against a corporate identity,
so this module governs *what the signed-in operator may do*, not authentication
itself. Authentication is delegated to the workstation session; the resolved
identity arrives via configuration or an approved reverse proxy header.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    TESTER = "tester"
    SENIOR_TESTER = "senior_tester"
    ADMINISTRATOR = "administrator"

    @classmethod
    def parse(cls, value: str | None) -> Role:
        if not value:
            return cls.VIEWER
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.VIEWER


class Permission(str, Enum):
    VIEW = "view"
    RUN_SCOUT = "run_scout"
    REVIEW = "review"
    EXPORT_CSV = "export_csv"
    SAVE_PROFILE = "save_profile"
    APPROVE_SAFETY_OVERRIDE = "approve_safety_override"
    MANAGE_CONNECTIONS = "manage_connections"
    MANAGE_RULES = "manage_rules"
    MANAGE_ADMIN = "manage_admin"
    SUBMIT_PRODUCTION = "submit_production"


_MATRIX: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW,
    },
    Role.TESTER: {
        Permission.VIEW,
        Permission.RUN_SCOUT,
        Permission.REVIEW,
        Permission.EXPORT_CSV,
        Permission.SAVE_PROFILE,
    },
    Role.SENIOR_TESTER: {
        Permission.VIEW,
        Permission.RUN_SCOUT,
        Permission.REVIEW,
        Permission.EXPORT_CSV,
        Permission.SAVE_PROFILE,
        Permission.APPROVE_SAFETY_OVERRIDE,
    },
    Role.ADMINISTRATOR: {
        Permission.VIEW,
        Permission.RUN_SCOUT,
        Permission.REVIEW,
        Permission.EXPORT_CSV,
        Permission.SAVE_PROFILE,
        Permission.APPROVE_SAFETY_OVERRIDE,
        Permission.MANAGE_CONNECTIONS,
        Permission.MANAGE_RULES,
        Permission.MANAGE_ADMIN,
    },
}

# SUBMIT_PRODUCTION is intentionally granted to nobody by default. Production
# submission is disabled platform-wide and requires an explicit, separately
# approved configuration change before this permission means anything.


def permissions_for(role: Role) -> set[Permission]:
    return set(_MATRIX.get(role, {Permission.VIEW}))


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


class PermissionDenied(Exception):
    def __init__(self, role: Role, permission: Permission) -> None:
        self.role = role
        self.permission = permission
        super().__init__(f"Role '{role.value}' is not permitted to perform '{permission.value}'.")


def require_permission(role: Role, permission: Permission) -> None:
    if not has_permission(role, permission):
        raise PermissionDenied(role, permission)


def describe_matrix() -> dict[str, list[str]]:
    return {role.value: sorted(p.value for p in perms) for role, perms in _MATRIX.items()}
