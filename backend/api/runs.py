"""Automation runs: preview, create, control and monitor."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import desc, select

from backend.api.deps import CurrentIdentity, DbSession, require
from backend.auth.identity import Identity
from backend.auth.roles import Permission
from backend.connectors.base import AdapterError
from backend.models.contracts import PIPELINE_STAGE_ORDER, RunRequest
from backend.models.orm import AutomationRun
from backend.settings import get_settings
from backend.workers.runner import RunError, get_run_manager

router = APIRouter(prefix="/runs", tags=["runs"])


def _serialise(run: AutomationRun, active: bool) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "stage": run.stage,
        "stage_order": PIPELINE_STAGE_ORDER,
        "completed_stages": run.completed_stages or [],
        "dry_run": run.dry_run,
        "active": active,
        "profile_id": run.profile_id,
        "connection_profile_id": run.connection_profile_id,
        "adapter": run.adapter_name,
        "query": run.query_json,
        "counters": {
            "records_discovered": run.records_discovered,
            "records_scanned": run.records_scanned,
            "records_processed": run.records_processed,
            "records_matched_country": run.records_matched_country,
            "records_matched_scenario": run.records_matched_scenario,
            "candidate_issue_count": run.candidate_issue_count,
            "blocking_error_count": run.blocking_error_count,
            "review_required_count": run.review_required_count,
            "duplicates_merged": run.duplicates_merged,
            "csv_rows_created": run.csv_rows_created,
            "error_count": run.error_count,
        },
        "versions": {
            "software": run.software_version,
            "contract": run.contract_version,
            "rules": run.rule_version,
            "model": run.model_version,
            "map": run.map_version,
        },
        "checkpoint": run.checkpoint or {},
        "message": run.message,
        "output_dir": run.output_dir,
        "elapsed_seconds": run.elapsed_seconds,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "created_by": run.created_by,
    }


def _run_or_404(session: Any, run_id: str) -> AutomationRun:
    run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No run '{run_id}'.")
    return run


@router.post("/preview")
def preview(request: RunRequest, identity: CurrentIdentity) -> dict[str, Any]:
    """Query summary and record estimate shown before RUN is pressed."""
    try:
        return get_run_manager().preview(request).model_dump(mode="json")
    except AdapterError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.user_message) from exc
    except RunError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_run(
    request: RunRequest,
    session: DbSession,
    identity: Identity = require(Permission.RUN_SCOUT),
) -> dict[str, Any]:
    manager = get_run_manager()
    try:
        run_id = manager.create_run(request, identity.user, identity.role.value)
    except (RunError, AdapterError) as exc:
        message = exc.user_message if isinstance(exc, AdapterError) else str(exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    manager.start(run_id, identity.user, identity.role.value)
    run = _run_or_404(session, run_id)
    return _serialise(run, active=True)


@router.get("")
def list_runs(session: DbSession, limit: int = 50) -> dict[str, Any]:
    manager = get_run_manager()
    runs = session.scalars(
        select(AutomationRun).order_by(desc(AutomationRun.created_at)).limit(max(1, min(limit, 500)))
    ).all()
    return {
        "runs": [_serialise(r, manager.is_active(r.run_id)) for r in runs],
        "resumable": manager.resumable_runs(),
    }


@router.get("/latest")
def latest_run(session: DbSession) -> dict[str, Any]:
    run = session.scalar(select(AutomationRun).order_by(desc(AutomationRun.created_at)).limit(1))
    if run is None:
        return {"run": None, "note": "No run has been executed yet."}
    return {"run": _serialise(run, get_run_manager().is_active(run.run_id))}


@router.get("/{run_id}")
def get_run(run_id: str, session: DbSession) -> dict[str, Any]:
    run = _run_or_404(session, run_id)
    return _serialise(run, get_run_manager().is_active(run_id))


@router.get("/{run_id}/progress")
def get_progress(run_id: str, session: DbSession) -> dict[str, Any]:
    """Latest progress frame; the WebSocket carries live updates."""
    from backend.workers.progress import get_progress_hub

    run = _run_or_404(session, run_id)
    latest = get_progress_hub().latest(run_id)
    return {
        "run_id": run_id,
        "live": latest,
        "persisted": _serialise(run, get_run_manager().is_active(run_id)),
    }


def _control(run_id: str, session: Any, identity: Any, action: str) -> dict[str, Any]:
    manager = get_run_manager()
    run = _run_or_404(session, run_id)
    try:
        if action == "pause":
            manager.pause(run_id, identity.user)
        elif action == "resume":
            if manager.is_active(run_id):
                manager.resume(run_id, identity.user)
            else:
                manager.start(run_id, identity.user, identity.role.value, resume=True)
        elif action == "cancel":
            manager.cancel(run_id, identity.user)
    except RunError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"run_id": run_id, "action": action, "status": run.status}


@router.post("/{run_id}/pause")
def pause_run(
    run_id: str, session: DbSession, identity: Identity = require(Permission.RUN_SCOUT)
) -> dict[str, Any]:
    return _control(run_id, session, identity, "pause")


@router.post("/{run_id}/resume")
def resume_run(
    run_id: str, session: DbSession, identity: Identity = require(Permission.RUN_SCOUT)
) -> dict[str, Any]:
    return _control(run_id, session, identity, "resume")


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str, session: DbSession, identity: Identity = require(Permission.RUN_SCOUT)
) -> dict[str, Any]:
    return _control(run_id, session, identity, "cancel")


@router.post("/{run_id}/repeat", status_code=status.HTTP_201_CREATED)
def repeat_run(
    run_id: str,
    session: DbSession,
    identity: Identity = require(Permission.RUN_SCOUT),
) -> dict[str, Any]:
    """Re-run with the frozen configuration of an earlier run.

    Reproducibility matters more than convenience here: the new run reuses the
    exact frozen request, so a repeat cannot silently pick up a changed profile.
    """
    previous = _run_or_404(session, run_id)
    request = RunRequest(**previous.frozen_config["request"])
    manager = get_run_manager()
    new_id = manager.create_run(request, identity.user, identity.role.value)
    manager.start(new_id, identity.user, identity.role.value)
    return _serialise(_run_or_404(session, new_id), active=True)


@router.get("/{run_id}/config")
def run_config(run_id: str, session: DbSession) -> dict[str, Any]:
    run = _run_or_404(session, run_id)
    return {
        "run_id": run_id,
        "frozen_config": run.frozen_config,
        "note": (
            "This is the configuration frozen at run creation. It is what makes the run "
            "reproducible; it is never rewritten by later configuration changes."
        ),
    }


@router.get("/-/submission-policy")
def submission_policy() -> dict[str, Any]:
    """Explicit statement of the production-submission posture."""
    settings = get_settings()
    return {
        "production_submission_enabled": settings.allow_production_submission,
        "source_access_mode": settings.source_access_mode,
        "browser_automation_enabled": settings.allow_browser_automation,
        "note": (
            "Production submission is disabled. The platform prepares and validates values and "
            "produces a submission preview, but it does not submit. Enabling submission requires "
            "an explicit, separately approved configuration change."
        ),
    }
