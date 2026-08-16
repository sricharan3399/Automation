"""Evidence viewer: listing, file serving and redaction preview."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import desc, select

from backend.api.deps import DbSession
from backend.evidence.redaction import get_redaction_policy
from backend.models.orm import AutomationRun, Event, Evidence
from backend.settings import get_settings

router = APIRouter(prefix="/evidence", tags=["evidence"])

#: Only these are ever served to the browser.
SERVEABLE_SUFFIXES = {".svg", ".json", ".png", ".jpg", ".jpeg", ".csv"}


@router.get("")
def list_evidence(
    session: DbSession,
    canonical_event_key: str | None = None,
    run_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    statement = select(Evidence, Event).join(Event, Evidence.event_pk == Event.event_pk)
    if canonical_event_key:
        statement = statement.where(Event.canonical_event_key == canonical_event_key)
    if run_id:
        run_pk = session.scalar(select(AutomationRun.run_pk).where(AutomationRun.run_id == run_id))
        statement = statement.where(Event.last_run_pk == run_pk)

    rows = session.execute(statement.order_by(desc(Evidence.created_at)).limit(max(1, min(limit, 1000)))).all()
    return {
        "items": [
            {
                "evidence_id": e.evidence_id,
                "canonical_event_key": ev.canonical_event_key,
                "event_reference": ev.anonymized_event_ref,
                "purpose": e.purpose,
                "kind": e.kind,
                "camera": e.camera,
                "t_rel_s": e.t_rel_s,
                "relative_path": e.relative_path,
                "content_hash": e.content_hash,
                "redacted": e.redacted,
                "approved": e.approved,
                "available": e.available,
                "unavailable_reason": e.unavailable_reason,
                "run_id": _run_id_for(session, ev.last_run_pk),
                "created_at": e.created_at,
            }
            for e, ev in rows
        ],
        "note": (
            "Items marked unavailable are recorded deliberately. An evidence manifest that silently "
            "omits a capture point would read like evidence that was reviewed and found unremarkable."
        ),
    }


def _run_id_for(session: Any, run_pk: int | None) -> str | None:
    if run_pk is None:
        return None
    return session.scalar(select(AutomationRun.run_id).where(AutomationRun.run_pk == run_pk))


@router.get("/redaction-policy")
def redaction_policy() -> dict[str, Any]:
    policy = get_redaction_policy()
    return {
        "enabled": policy.enabled,
        "fail_closed": policy.fail_closed,
        "image_regions": policy.image_regions,
        "pseudonymised_fields": sorted(policy.pseudonymise_fields),
        "coordinate_precision": {
            "enabled": policy.round_coordinates,
            "decimals": policy.coordinate_decimals,
            "fields": sorted(policy.coordinate_fields),
        },
        "patterns": [name for name, _ in policy.patterns],
        "note": (
            "Redaction is fail-closed: when it is required but cannot be applied, the export is "
            "refused rather than shipped unredacted."
        ),
    }


@router.post("/redaction-preview")
def redaction_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Show exactly what redaction would do to a payload, writing nothing."""
    return get_redaction_policy().preview(payload)


@router.get("/file/{run_id}/{event_ref}/{filename}")
def evidence_file(run_id: str, event_ref: str, filename: str, session: DbSession) -> FileResponse:
    run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
    base = Path(run.output_dir) if (run and run.output_dir) else get_settings().output_dir / f"run_{run_id}"
    directory = (base / "evidence" / event_ref).resolve()
    target = (directory / filename).resolve()

    # Path traversal guard plus an allow-list of servable types.
    if not str(target).startswith(str(directory.parent)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid evidence path.")
    if target.suffix.lower() not in SERVEABLE_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"'{target.suffix}' evidence is not served over HTTP.",
        )
    if not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No evidence file '{filename}'.")

    media = {
        ".svg": "image/svg+xml",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".csv": "text/csv",
    }[target.suffix.lower()]
    return FileResponse(target, media_type=media)
