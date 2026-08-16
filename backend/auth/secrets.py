"""Secret resolution.

Resolution order:

    1. OS credential store (Windows Credential Manager / macOS Keychain /
       Secret Service) when the optional ``keyring`` package is installed
    2. environment variable (populated by the approved secret injector, or by
       a gitignored ``.env`` during approved local development)

Secrets are never written to the database, never returned by the API, and
never included in configuration profiles or run outputs. Callers receive the
value or a :class:`SecretNotAvailable`; there is no "return a placeholder"
path, because a placeholder would let a caller believe it is authenticated.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

KEYRING_SERVICE = "av-test-automation"


class SecretNotAvailable(Exception):
    """Raised when a required secret cannot be resolved from any source."""

    def __init__(self, key: str, tried: list[str]) -> None:
        self.key = key
        self.tried = tried
        super().__init__(
            f"Secret '{key}' is not available. Tried: {', '.join(tried)}. "
            "Store it in the OS credential store or inject it via the approved secret manager."
        )


def _from_keyring(key: str) -> str | None:
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, key)
    except Exception as exc:  # pragma: no cover - platform dependent
        log.debug("Credential store lookup failed for %s: %s", key, exc)
        return None
    return value or None


def resolve_secret(key: str, *, required: bool = True) -> str | None:
    """Return the secret for ``key`` or raise :class:`SecretNotAvailable`.

    ``key`` is both the credential-store entry name and the environment
    variable name.
    """
    tried: list[str] = []

    value = _from_keyring(key)
    tried.append("os_credential_store")
    if value:
        return value

    value = os.environ.get(key)
    tried.append("environment")
    if value:
        return value

    if required:
        raise SecretNotAvailable(key, tried)
    return None


def secret_is_available(key: str) -> bool:
    """Non-raising availability probe, safe to expose to the dashboard.

    Returns only whether a value exists - never the value itself.
    """
    try:
        return resolve_secret(key, required=False) is not None
    except Exception:  # pragma: no cover - defensive
        return False


def keyring_available() -> bool:
    try:
        import keyring  # noqa: F401  # type: ignore[import-not-found]
    except Exception:
        return False
    return True
