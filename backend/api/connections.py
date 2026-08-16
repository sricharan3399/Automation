"""Connection manager, schema discovery and field mapping."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.deps import CurrentIdentity, DbSession, adapter_http_error, require
from backend.audit.logger import (
    ACTION_CONNECTION_TESTED,
    ACTION_CONNECTION_UPDATED,
    ACTION_SCHEMA_DISCOVERED,
    audit,
)
from backend.auth.identity import Identity
from backend.auth.roles import Permission
from backend.auth.secrets import secret_is_available
from backend.connectors.base import AdapterError
from backend.connectors.metadata import MetadataResolver
from backend.connectors.registry import ConnectionManager, available_adapters
from backend.settings import get_settings

router = APIRouter(prefix="/connections", tags=["connections"])

#: Keys that must never be echoed back from a connection profile.
SECRET_KEYS = {"token", "api_key", "password", "client_secret", "secret", "private_key"}


def _sanitise(settings_json: dict[str, Any]) -> dict[str, Any]:
    """Strip anything secret-looking before a profile leaves the process."""
    out: dict[str, Any] = {}
    for key, value in (settings_json or {}).items():
        if key.lower() in SECRET_KEYS or any(s in key.lower() for s in SECRET_KEYS):
            out[key] = "***withheld***" if value else None
        else:
            out[key] = value
    return out


def _serialise(profile: Any) -> dict[str, Any]:
    credential_key = (profile.settings_json or {}).get("credential_key")
    return {
        "connection_id": profile.connection_id,
        "display_name": profile.display_name,
        "kind": profile.kind,
        "adapter": profile.adapter,
        "integration_type": profile.integration_type,
        "enabled": profile.enabled,
        "configured": bool(profile.settings_json) and profile.last_status != "NOT_CONFIGURED",
        "last_status": profile.last_status,
        "last_tested_at": profile.last_tested_at,
        "last_latency_ms": profile.last_latency_ms,
        "last_error": profile.last_error,
        "api_version": profile.api_version,
        "schema_version": profile.schema_version,
        "permissions": profile.permissions_json or [],
        "settings": _sanitise(profile.settings_json or {}),
        "credential_available": secret_is_available(credential_key) if credential_key else None,
        "has_field_mapping": bool((profile.field_mapping_json or {}).get("mapping")),
        "read_only": True,
        "updated_at": profile.updated_at,
    }


class ConnectionUpdate(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    integration_type: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class FieldMappingUpdate(BaseModel):
    mapping: dict[str, str]


@router.get("")
def list_connections(session: DbSession) -> dict[str, Any]:
    manager = ConnectionManager(session)
    settings = get_settings()
    return {
        "connections": [_serialise(p) for p in manager.profiles()],
        "available_adapters": available_adapters(),
        "operating_mode": settings.operating_mode,
        "source_access_mode": settings.source_access_mode,
        "note": (
            "All source integrations are read-only. The adapter interface exposes no method that "
            "can create, modify or delete anything in a source system."
        ),
    }


@router.get("/{connection_id}")
def get_connection(connection_id: str, session: DbSession) -> dict[str, Any]:
    profile = ConnectionManager(session).profile(connection_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No connection '{connection_id}'.")
    return _serialise(profile)


@router.put("/{connection_id}")
def update_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    session: DbSession,
    identity: Identity = require(Permission.MANAGE_CONNECTIONS),
) -> dict[str, Any]:
    manager = ConnectionManager(session)
    profile = manager.profile(connection_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No connection '{connection_id}'.")

    before = _serialise(profile)
    if payload.display_name is not None:
        profile.display_name = payload.display_name
    if payload.enabled is not None:
        profile.enabled = payload.enabled
    if payload.integration_type is not None:
        profile.integration_type = payload.integration_type
    if payload.settings:
        rejected = [k for k in payload.settings if k.lower() in SECRET_KEYS]
        if rejected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Secret values are never stored in a connection profile. "
                    f"Remove {', '.join(rejected)} and supply a 'credential_key' naming the entry in "
                    "the OS credential store or the injected environment variable instead."
                ),
            )
        profile.settings_json = {**(profile.settings_json or {}), **payload.settings}

    session.add(profile)
    session.flush()
    after = _serialise(profile)

    audit(
        session,
        ACTION_CONNECTION_UPDATED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="connection",
        entity_ref=connection_id,
        before=before,
        after=after,
        detail="Connection settings updated.",
    )
    return after


@router.post("/{connection_id}/test")
def test_connection(
    connection_id: str,
    session: DbSession,
    identity: CurrentIdentity,
) -> dict[str, Any]:
    manager = ConnectionManager(session)
    result = manager.test(connection_id)
    audit(
        session,
        ACTION_CONNECTION_TESTED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="connection",
        entity_ref=connection_id,
        after=result.to_dict(),
        detail=result.message,
    )
    return result.to_dict()


@router.post("/test-all")
def test_all(session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    results = ConnectionManager(session).test_all()
    for connection_id, result in results.items():
        audit(
            session,
            ACTION_CONNECTION_TESTED,
            actor=identity.user,
            actor_role=identity.role.value,
            entity_type="connection",
            entity_ref=connection_id,
            after=result.to_dict(),
        )
    return {connection_id: result.to_dict() for connection_id, result in results.items()}


@router.post("/{connection_id}/discover-schema")
def discover_schema(
    connection_id: str,
    session: DbSession,
    identity: Identity = require(Permission.MANAGE_CONNECTIONS),
) -> dict[str, Any]:
    """Inspect the source and propose a source-field -> canonical-field mapping."""
    manager = ConnectionManager(session)
    profile = manager.profile(connection_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No connection '{connection_id}'.")

    try:
        adapter = manager.adapter_for(connection_id)
        adapter.authenticate()
        schema = MetadataResolver(adapter, profile).discover_schema()
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    payload = schema.to_dict()
    profile.discovered_schema_json = payload
    profile.schema_version = schema.schema_version
    session.add(profile)

    audit(
        session,
        ACTION_SCHEMA_DISCOVERED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="connection",
        entity_ref=connection_id,
        after={"field_count": len(schema.fields), "schema_version": schema.schema_version},
        detail=schema.note or "Schema discovered.",
    )
    return payload


@router.get("/{connection_id}/schema")
def get_schema(connection_id: str, session: DbSession) -> dict[str, Any]:
    profile = ConnectionManager(session).profile(connection_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No connection '{connection_id}'.")
    return {
        "discovered_schema": profile.discovered_schema_json or {},
        "confirmed_mapping": (profile.field_mapping_json or {}).get("mapping", {}),
    }


@router.put("/{connection_id}/field-mapping")
def update_field_mapping(
    connection_id: str,
    payload: FieldMappingUpdate,
    session: DbSession,
    identity: Identity = require(Permission.MANAGE_CONNECTIONS),
) -> dict[str, Any]:
    """Save the mapping an administrator confirmed in the mapping editor."""
    manager = ConnectionManager(session)
    profile = manager.profile(connection_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No connection '{connection_id}'.")

    duplicates = [
        canonical
        for canonical in set(payload.mapping.values())
        if list(payload.mapping.values()).count(canonical) > 1
    ]
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Each canonical field may be mapped from only one source field. "
                f"Duplicated: {', '.join(sorted(set(duplicates)))}."
            ),
        )

    before = (profile.field_mapping_json or {}).get("mapping", {})
    profile.field_mapping_json = {"mapping": payload.mapping}
    session.add(profile)

    audit(
        session,
        ACTION_CONNECTION_UPDATED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="connection.field_mapping",
        entity_ref=connection_id,
        before={"mapping": before},
        after={"mapping": payload.mapping},
        detail=f"Field mapping updated ({len(payload.mapping)} field(s)).",
    )
    return {"mapping": payload.mapping}


@router.get("/{connection_id}/datasets")
def datasets(connection_id: str, session: DbSession, project: str | None = None) -> dict[str, Any]:
    manager = ConnectionManager(session)
    try:
        adapter = manager.adapter_for(connection_id)
        adapter.authenticate()
        return {"projects": adapter.get_projects(), "datasets": adapter.get_datasets(project)}
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
