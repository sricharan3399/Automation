"""Configuration profiles (saved dashboard setups)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.api.deps import DbSession, require
from backend.audit.logger import ACTION_PROFILE_SAVED, audit
from backend.auth.identity import Identity
from backend.auth.roles import Permission
from backend.models.contracts import ScoutQuery, SensorConfiguration
from backend.models.orm import ConfigurationProfile

router = APIRouter(prefix="/profiles", tags=["profiles"])

#: Any of these appearing in a saved profile is a bug, so the API refuses it.
FORBIDDEN_KEYS = {"token", "api_key", "password", "secret", "client_secret", "credential", "private_key"}


class ProfilePayload(BaseModel):
    profile_id: str
    name: str
    description: str = ""
    query: ScoutQuery = Field(default_factory=ScoutQuery)
    sensor_config: SensorConfiguration = Field(default_factory=SensorConfiguration.default)
    csv_template_id: str = "germany_bus_test"
    csv_columns: list[str] = Field(default_factory=list)
    rule_overrides: dict[str, bool] = Field(default_factory=dict)
    threshold_overrides: dict[str, float] = Field(default_factory=dict)
    evidence_config: dict[str, Any] = Field(default_factory=dict)
    connection_profile_id: str | None = None


def _serialise(profile: ConfigurationProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "name": profile.name,
        "description": profile.description,
        "version": profile.version,
        "is_builtin": profile.is_builtin,
        "query": profile.query_json,
        "sensor_config": profile.sensor_config_json,
        "csv_template_id": profile.csv_template_id,
        "csv_columns": profile.csv_columns_json,
        "rule_overrides": profile.rule_overrides_json,
        "threshold_overrides": profile.threshold_overrides_json,
        "evidence_config": profile.evidence_config_json,
        "connection_profile_id": profile.connection_profile_id,
        "executed_count": profile.executed_count,
        "created_by": profile.created_by,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _reject_secrets(payload: ProfilePayload) -> None:
    blob = payload.model_dump_json().lower()
    hits = [key for key in FORBIDDEN_KEYS if f'"{key}"' in blob]
    if hits:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Configuration profiles never store credentials. Remove "
                f"{', '.join(sorted(hits))} and reference a connection profile instead."
            ),
        )


@router.get("")
def list_profiles(session: DbSession) -> dict[str, Any]:
    profiles = session.scalars(select(ConfigurationProfile).order_by(ConfigurationProfile.name)).all()
    return {"profiles": [_serialise(p) for p in profiles]}


@router.get("/{profile_id}")
def get_profile(profile_id: str, session: DbSession) -> dict[str, Any]:
    profile = session.scalar(select(ConfigurationProfile).where(ConfigurationProfile.profile_id == profile_id))
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No profile '{profile_id}'.")
    return _serialise(profile)


@router.put("/{profile_id}")
def save_profile(
    profile_id: str,
    payload: ProfilePayload,
    session: DbSession,
    identity: Identity = require(Permission.SAVE_PROFILE),
) -> dict[str, Any]:
    _reject_secrets(payload)
    if payload.profile_id != profile_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The profile_id in the body must match the one in the path.",
        )

    profile = session.scalar(select(ConfigurationProfile).where(ConfigurationProfile.profile_id == profile_id))
    created = profile is None
    before = None if profile is None else _serialise(profile)

    if profile is None:
        profile = ConfigurationProfile(profile_id=profile_id, created_by=identity.user)

    if profile.is_builtin:
        # Built-ins stay pristine so an upgrade can rely on them; editing one
        # creates a copy instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{profile.name}' is a bundled profile and cannot be overwritten. "
                "Save it under a new profile id to keep your changes."
            ),
        )

    profile.name = payload.name
    profile.description = payload.description
    profile.query_json = payload.query.model_dump(mode="json")
    profile.sensor_config_json = payload.sensor_config.model_dump(mode="json")
    profile.csv_template_id = payload.csv_template_id
    profile.csv_columns_json = payload.csv_columns
    profile.rule_overrides_json = payload.rule_overrides
    profile.threshold_overrides_json = payload.threshold_overrides
    profile.evidence_config_json = payload.evidence_config
    profile.connection_profile_id = payload.connection_profile_id
    session.add(profile)
    session.flush()

    after = _serialise(profile)
    audit(
        session,
        ACTION_PROFILE_SAVED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="configuration_profile",
        entity_ref=profile_id,
        before=before,
        after=after,
        detail=f"Profile {'created' if created else 'updated'}.",
    )
    return after


@router.delete("/{profile_id}")
def delete_profile(
    profile_id: str,
    session: DbSession,
    identity: Identity = require(Permission.SAVE_PROFILE),
) -> dict[str, Any]:
    profile = session.scalar(select(ConfigurationProfile).where(ConfigurationProfile.profile_id == profile_id))
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No profile '{profile_id}'.")
    if profile.is_builtin:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bundled profiles cannot be deleted.")

    before = _serialise(profile)
    session.delete(profile)
    audit(
        session,
        ACTION_PROFILE_SAVED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="configuration_profile",
        entity_ref=profile_id,
        before=before,
        after=None,
        detail="Profile deleted.",
    )
    return {"deleted": profile_id}
