"""Administration: platform settings, roles and retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.api.deps import CurrentIdentity, DbSession, require
from backend.audit.logger import ACTION_CONFIG_CHANGED, ACTION_SUBMISSION_REFUSED, audit
from backend.auth.identity import Identity
from backend.auth.roles import Permission, describe_matrix
from backend.configstore import get_config_store
from backend.models.orm import AutomationRun, Event
from backend.settings import get_settings, reset_settings_cache
from backend.version import CONTRACT_VERSION, METHOD_VERSION, SOFTWARE_VERSION

router = APIRouter(prefix="/admin", tags=["admin"])


class ReloadRequest(BaseModel):
    reload_config: bool = True


@router.get("/settings")
def platform_settings(identity: Identity = require(Permission.MANAGE_ADMIN)) -> dict[str, Any]:
    settings = get_settings()
    store = get_config_store()
    return {
        "application": {
            "name": settings.app_name,
            "environment": settings.environment,
            "software_version": SOFTWARE_VERSION,
            "contract_version": CONTRACT_VERSION,
            "method_version": METHOD_VERSION,
            "rule_version": store.rule_version_signature(),
        },
        "mode": {
            "operating_mode": settings.operating_mode,
            "source_access_mode": settings.source_access_mode,
            "allow_production_submission": settings.allow_production_submission,
            "allow_browser_automation": settings.allow_browser_automation,
            "force_dry_run_on_first_execution": settings.force_dry_run_on_first_execution,
        },
        "countries": settings.raw.get("countries", {}),
        "processing": settings.section("processing"),
        "geometry": settings.section("geometry"),
        "synchronization": settings.section("synchronization"),
        "perception": settings.section("perception"),
        "tracking": settings.section("tracking"),
        "behavior": settings.section("behavior"),
        "evidence": settings.section("evidence"),
        "review": settings.section("review"),
        "export": settings.section("export"),
        "storage": {
            "database_url_scheme": settings.database_url.split(":")[0],
            "output_dir": str(settings.output_dir),
            "checkpoint_dir": str(settings.checkpoint_dir),
            "retention_days": settings.retention_days,
        },
        "csv_templates": [t.to_dict() for t in store.csv_templates().values()],
        "roles": describe_matrix(),
        "editing_note": (
            "Runtime configuration is edited in config/*.yaml and applied with Reload. "
            "Settings are versioned into every run so results stay reproducible."
        ),
        "requested_by": identity.user,
    }


@router.post("/reload")
def reload_configuration(
    payload: ReloadRequest,
    session: DbSession,
    identity: Identity = require(Permission.MANAGE_ADMIN),
) -> dict[str, Any]:
    store = get_config_store()
    before = {
        "rule_catalogue_version": store.rule_catalogue_version,
        "operating_mode": get_settings().operating_mode,
    }
    if payload.reload_config:
        store.reload()
    reset_settings_cache()
    after = {
        "rule_catalogue_version": get_config_store().rule_catalogue_version,
        "operating_mode": get_settings().operating_mode,
    }
    audit(
        session,
        ACTION_CONFIG_CHANGED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="configuration",
        entity_ref="config/*.yaml",
        before=before,
        after=after,
        detail="Configuration reloaded from disk.",
    )
    return {"reloaded": True, "before": before, "after": after}


@router.get("/roles")
def roles(identity: CurrentIdentity) -> dict[str, Any]:
    return {
        "matrix": describe_matrix(),
        "current": identity.to_dict(),
        "note": (
            "The submit_production permission is granted to no role by default. Production "
            "submission is disabled platform-wide and requires a separately approved change."
        ),
    }


@router.get("/retention")
def retention(session: DbSession, identity: Identity = require(Permission.MANAGE_ADMIN)) -> dict[str, Any]:
    """Report what the retention policy *would* remove. Nothing is deleted here."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    old_runs = session.scalar(
        select(func.count(AutomationRun.run_pk)).where(AutomationRun.created_at < cutoff)
    ) or 0
    old_events = session.scalar(select(func.count(Event.event_pk)).where(Event.updated_at < cutoff)) or 0
    return {
        "retention_days": settings.retention_days,
        "cutoff": cutoff.isoformat(),
        "runs_older_than_cutoff": old_runs,
        "events_older_than_cutoff": old_events,
        "note": (
            "This is a report, not an action. Deletion of AV records is never automatic; it is "
            "performed deliberately under the approved data-retention process."
        ),
    }


@router.post("/production-submission")
def production_submission(
    session: DbSession,
    identity: Identity = require(Permission.MANAGE_ADMIN),
) -> dict[str, Any]:
    """Explicitly refuse uncontrolled production submission."""
    audit(
        session,
        ACTION_SUBMISSION_REFUSED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="submission",
        entity_ref="production",
        detail="Production submission was requested and refused: the capability is disabled.",
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "message": (
                "Production submission is disabled and is not implemented as a one-click action. "
                "The approved flow is: review -> validation gate -> submission preview -> explicit "
                "reviewer confirmation -> submit -> read-back verification -> audit. Enabling it "
                "requires a separately approved configuration change and an approved integration."
            ),
            "allow_production_submission": get_settings().allow_production_submission,
        },
    )
