"""CSV templates, preview, export readiness and downloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.api.deps import DbSession, require
from backend.audit.logger import ACTION_EXPORT_BLOCKED, ACTION_EXPORT_GENERATED, audit
from backend.auth.identity import Identity
from backend.auth.roles import Permission
from backend.configstore import get_config_store
from backend.database.repository import reviews_for_keys
from backend.models.contracts import ReviewPackage
from backend.models.orm import AutomationRun, Event
from backend.reports.csv_builder import CsvBuilder
from backend.reports.export_record import build_export_row
from backend.settings import get_settings
from backend.validation.engine import abnormality_categories
from backend.version import METHOD_VERSION, SOFTWARE_VERSION

router = APIRouter(prefix="/reports", tags=["reports"])


class ExportRequest(BaseModel):
    run_id: str | None = None
    canonical_event_keys: list[str] = Field(default_factory=list)
    template_id: str = "germany_bus_test"
    columns: list[str] = Field(default_factory=list)
    country_code: str | None = None
    preview_only: bool = True


@router.get("/templates")
def templates() -> dict[str, Any]:
    store = get_config_store()
    return {
        "templates": [t.to_dict() for t in store.csv_templates().values()],
        "default": get_settings().section("export").get("default_template", "germany_bus_test"),
        "export_settings": get_settings().section("export"),
    }


def _packages_for(session: Any, request: ExportRequest) -> list[tuple[Event, ReviewPackage]]:
    """Rebuild review packages from stored analysis for export."""
    statement = select(Event)
    if request.run_id:
        run_pk = session.scalar(select(AutomationRun.run_pk).where(AutomationRun.run_id == request.run_id))
        if run_pk is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No run '{request.run_id}'.")
        statement = statement.where(Event.last_run_pk == run_pk)
    if request.canonical_event_keys:
        statement = statement.where(Event.canonical_event_key.in_(request.canonical_event_keys))

    out: list[tuple[Event, ReviewPackage]] = []
    for event in session.scalars(statement.order_by(Event.event_pk)).all():
        package = _rebuild_package(session, event)
        if package is not None:
            out.append((event, package))
    return out


def _rebuild_package(session: Any, event: Event) -> ReviewPackage | None:
    """Reconstruct the contract payload the CSV builder expects."""
    from backend.models.contracts import (
        BehaviorAnalysis,
        EventMetadata,
        GeometryResult,
        RecordStatus,
        SceneAnalysis,
        SynchronizationReport,
        ValidationReport,
    )
    from backend.models.orm import Evidence, FieldRecommendation, ValidationResult

    analysis = event.analysis_json or {}
    try:
        metadata = EventMetadata(**(event.metadata_json or {"event_id": event.source_event_id}))
    except Exception:
        metadata = EventMetadata(event_id=event.source_event_id)

    recommendations = session.scalars(
        select(FieldRecommendation).where(FieldRecommendation.event_pk == event.event_pk)
    ).all()
    validations = session.scalars(
        select(ValidationResult).where(ValidationResult.event_pk == event.event_pk)
    ).all()
    evidence_rows = session.scalars(select(Evidence).where(Evidence.event_pk == event.event_pk)).all()

    from backend.models.contracts import (
        ConfidenceBand,
        ConfidenceExplanation,
        EvidenceItem,
        FieldRecommendationContract,
        Severity,
        ValidationOutcome,
    )

    package = ReviewPackage(
        canonical_event_key=event.canonical_event_key,
        anonymized_event_ref=event.anonymized_event_ref,
        anonymized_session_ref=event.anonymized_session_ref,
        anonymized_job_ref=event.anonymized_job_ref,
        metadata=metadata,
        synchronization=SynchronizationReport(**analysis.get("synchronization", {})),
        trajectory_summary=analysis.get("trajectory_summary", {}),
        geometry=GeometryResult(**analysis.get("geometry", {})),
        scene=SceneAnalysis(**analysis.get("scene", {})),
        behavior=BehaviorAnalysis(**analysis.get("behavior", {})),
        validation=ValidationReport(
            outcomes=[
                ValidationOutcome(
                    rule_id=v.rule_name,
                    category=v.category,
                    severity=Severity(v.severity),
                    passed=v.passed,
                    skipped=v.skipped,
                    skip_reason=v.skip_reason,
                    message=v.message,
                    observed=v.observed_json or {},
                    recommended_correction=v.recommended_correction,
                    blocks_export=v.blocks_export,
                    requires_review=v.requires_review,
                    rule_version=v.rule_version,
                )
                for v in validations
            ]
        ),
        recommendations=[
            FieldRecommendationContract(
                field_name=r.field_name,
                original_value=(r.original_value_json or {}).get("value"),
                recommended_value=(r.recommended_value_json or {}).get("value"),
                alternatives=r.alternatives_json or [],
                confidence=r.confidence,
                band=ConfidenceBand(r.confidence_band),
                explanation=ConfidenceExplanation(**(r.confidence_components or {})),
                reason=r.explanation,
                method=r.method,
                auto_selected=r.auto_selected,
                safety_critical=r.safety_critical,
                status=RecordStatus(r.status),
                model_or_rule_version=r.model_or_rule_version,
            )
            for r in recommendations
        ],
        evidence=[
            EvidenceItem(
                evidence_id=e.evidence_id,
                purpose=e.purpose,
                kind=e.kind,  # type: ignore[arg-type]
                camera=e.camera,
                t_rel_s=e.t_rel_s,
                relative_path=e.relative_path,
                content_hash=e.content_hash,
                redacted=e.redacted,
                approved=e.approved,
                available=e.available,
                unavailable_reason=e.unavailable_reason,
            )
            for e in evidence_rows
        ],
        overall_confidence=event.overall_confidence or 0.0,
        automation_recommendation=analysis.get("automation_recommendation", ""),
        status=RecordStatus(event.status),
        blocking_error_count=event.blocking_error_count,
        review_required=event.review_required,
        record_version=event.record_version,
        map_version=(analysis.get("geometry", {}) or {}).get("map_version"),
        is_synthetic=bool(analysis.get("is_synthetic")),
        # Version stamps are mandatory CSV columns, so they must survive the
        # round trip through the database, not be silently defaulted to blank.
        software_version=SOFTWARE_VERSION,
        model_version=METHOD_VERSION,
        rule_version=next(
            (v.rule_version for v in validations if v.rule_version),
            get_config_store().rule_version_signature(),
        ),
    )
    # Reuse the canonical mapping so an export never disagrees with the run
    # about which abnormality vocabulary a rule category maps onto.
    package.abnormality_categories = abnormality_categories(package.validation)
    return package


@router.post("/preview")
def preview(request: ExportRequest, session: DbSession) -> dict[str, Any]:
    """Rendered rows plus export readiness, before anything is written."""
    pairs = _packages_for(session, request)
    if not pairs:
        return {
            "rows": [],
            "headers": [],
            "total_rows": 0,
            "readiness": {"ready": False, "blocking_errors": 0, "warnings": 0, "passed": 0},
            "note": (
                "No records match this selection. Run a scout first, or widen the run/record filter."
            ),
        }

    reviews = reviews_for_keys(session, {p.canonical_event_key for _, p in pairs})
    rows = [
        build_export_row(
            package,
            reviews.get(package.canonical_event_key, {}),
            evidence_folder=f"evidence/{package.anonymized_event_ref}",
            evidence_manifest="evidence_manifest.csv",
        )
        for _, package in pairs
    ]

    builder = CsvBuilder(request.template_id, request.columns or None)
    readiness, issues = builder.validate(rows, request.country_code)
    payload = builder.preview(rows)
    payload["readiness"] = readiness.model_dump(mode="json")
    payload["issues"] = [i.to_dict() for i in issues[:200]]
    payload["export_blocked"] = not readiness.ready
    return payload


@router.post("/export")
def export(
    request: ExportRequest,
    session: DbSession,
    identity: Identity = require(Permission.EXPORT_CSV),
) -> dict[str, Any]:
    """Write a CSV for the selected records into the run output directory."""
    pairs = _packages_for(session, request)
    if not pairs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No records match this selection.")

    reviews = reviews_for_keys(session, {p.canonical_event_key for _, p in pairs})
    rows = [
        build_export_row(
            package,
            reviews.get(package.canonical_event_key, {}),
            evidence_folder=f"evidence/{package.anonymized_event_ref}",
            evidence_manifest="evidence_manifest.csv",
        )
        for _, package in pairs
    ]

    builder = CsvBuilder(request.template_id, request.columns or None)
    readiness, issues = builder.validate(rows, request.country_code)

    from backend.reports.csv_validation import split_rows, write_rejected_records

    exportable, rejected = split_rows(rows, issues)
    output_dir = get_settings().output_dir / (f"run_{request.run_id}" if request.run_id else "manual_exports")
    output_dir.mkdir(parents=True, exist_ok=True)

    rejected_path = output_dir / "rejected_records.csv"
    write_rejected_records(rejected_path, rejected)

    if not readiness.ready and bool(builder.export_config.get("block_export_on_blocking_errors", True)):
        audit(
            session,
            ACTION_EXPORT_BLOCKED,
            actor=identity.user,
            actor_role=identity.role.value,
            entity_type="export",
            entity_ref=request.run_id or "manual",
            after=readiness.model_dump(mode="json"),
            detail=f"Export refused: {readiness.blocking_errors} blocking error(s).",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"CSV NOT READY: {readiness.blocking_errors} blocking error(s) across "
                    f"{readiness.rejected_rows} record(s). The rejected records were written to "
                    f"{rejected_path.name} with the rule that rejected each one."
                ),
                "readiness": readiness.model_dump(mode="json"),
                "rejected_records_path": str(rejected_path),
            },
        )

    results_path = output_dir / "results.csv"
    written = builder.write_csv(results_path, exportable)
    audit(
        session,
        ACTION_EXPORT_GENERATED,
        actor=identity.user,
        actor_role=identity.role.value,
        entity_type="export",
        entity_ref=request.run_id or "manual",
        after={**readiness.model_dump(mode="json"), "rows_written": written},
        detail=f"CSV exported with {written} row(s).",
    )
    return {
        "results_csv": str(results_path),
        "rejected_records_csv": str(rejected_path),
        "rows_written": written,
        "rejected_records": len(rejected),
        "readiness": readiness.model_dump(mode="json"),
    }


@router.get("/runs/{run_id}/files")
def run_files(run_id: str, session: DbSession) -> dict[str, Any]:
    run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No run '{run_id}'.")
    directory = Path(run.output_dir) if run.output_dir else get_settings().output_dir / f"run_{run_id}"
    if not directory.is_dir():
        return {"run_id": run_id, "directory": str(directory), "files": [], "note": "No output directory exists for this run."}
    files = [
        {"name": p.name, "size_bytes": p.stat().st_size, "modified": p.stat().st_mtime}
        for p in sorted(directory.iterdir())
        if p.is_file()
    ]
    return {"run_id": run_id, "directory": str(directory), "files": files}


@router.get("/runs/{run_id}/download/{filename}")
def download(run_id: str, filename: str, session: DbSession) -> FileResponse:
    run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No run '{run_id}'.")
    directory = (Path(run.output_dir) if run.output_dir else get_settings().output_dir / f"run_{run_id}").resolve()
    target = (directory / filename).resolve()
    # Path traversal guard: the resolved target must stay inside the run directory.
    if not str(target).startswith(str(directory)) or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No file '{filename}' in this run.")
    return FileResponse(target, filename=filename)
