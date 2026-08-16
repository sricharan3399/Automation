"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.auth.identity import Identity, resolve_identity
from backend.auth.roles import Permission, PermissionDenied, require_permission
from backend.connectors.base import (
    AdapterAuthError,
    AdapterError,
    AdapterNotConfigured,
    AdapterPermissionError,
    AdapterUnavailable,
)
from backend.database.session import get_session


def db_session() -> Generator[Session, None, None]:
    yield from get_session()


def current_identity(
    x_av_user: Annotated[str | None, Header()] = None,
    x_av_role: Annotated[str | None, Header()] = None,
) -> Identity:
    return resolve_identity(x_av_user, x_av_role)


DbSession = Annotated[Session, Depends(db_session)]
CurrentIdentity = Annotated[Identity, Depends(current_identity)]


def require(permission: Permission):
    """Dependency factory enforcing one permission."""

    def dependency(identity: CurrentIdentity) -> Identity:
        try:
            require_permission(identity.role, permission)
        except PermissionDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return identity

    return Depends(dependency)


def adapter_http_error(exc: AdapterError) -> HTTPException:
    """Translate an adapter failure into an HTTP response with a usable message.

    The dashboard never shows a bare status code; every error carries text a
    tester can act on.
    """
    if isinstance(exc, AdapterNotConfigured):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, AdapterAuthError):
        code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(exc, AdapterPermissionError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, AdapterUnavailable):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=code,
        detail={"message": exc.user_message, "retryable": exc.retryable, "detail": getattr(exc, "detail", None)},
    )
