"""Local identity, role-based access control and secret resolution."""

from backend.auth.roles import Permission, Role, has_permission, require_permission
from backend.auth.secrets import SecretNotAvailable, resolve_secret

__all__ = [
    "Role",
    "Permission",
    "has_permission",
    "require_permission",
    "resolve_secret",
    "SecretNotAvailable",
]
