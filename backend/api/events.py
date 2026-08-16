"""Event Explorer and Event Detail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from backend.api.deps import DbSession
from backend.models.orm import (
    AuditEvent,
    Detection,
    EgoPose,
    Event,
    Evidence,
    FieldRecommendation,
    MapFeature,
    Review,
    SensorStream,
    ValidationResult,
)

router = APIRouter(prefix="/events", tags=["events"])


def _summary(event: Event) -> dict[str, Any]:
    analysis = event.analysis_json or {}
    return {
        "canonical_event_key": event.canonical_event_key,
        "event_reference": event.anonymized_event_ref,
        "session_reference": event.anonymized_session_ref,
        "country": event.country,
        "country_code": event.country_code,
        "region": event.region,
        "city": event.city,
        "event_type": event.event_type,
        "event_time": event.approx_event_time,
        "object_types": event.object_types or [],
        "bus_type": event.bus_type,
        "scenario_tags": event.scenario_tags or [],
        "road_type": event.road_type,
        "lane_count": event.lane_count,
        "lane_relation": event.lane_relation,
        "intersection_type": event.intersection_type,
        "intersection_complexity": event.intersection_complexity,
        "weather": event.weather,
        "lighting": event.lighting,
        "status": event.status,
        "record_version": event.record_version,
        "overall_confidence": event.overall_confidence,
        "blocking_error_count": event.blocking_error_count,
        "review_required": event.review_required,
        # Filled in by the caller from the event's failed validation results.
        "abnormality_categories": [],
        "synchronization_quality": (analysis.get("synchronization") or {}).get("quality"),
        "is_synthetic": bool(analysis.get("is_synthetic")),
        "updated_at": event.updated_at,
    }


@router.get("")
def list_events(
    session: DbSession,
    search: str | None = None,
    country_code: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    road_type: str | None = None,
    intersection_type: str | None = None,
    weather: str | None = None,
    lighting: str | None = None,
    object_type: str | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    review_required: bool | None = None,
    has_blocking_errors: bool | None = None,
    run_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Searchable, filterable event table backing the Event Explorer."""
    statement = select(Event)

    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                Event.anonymized_event_ref.ilike(pattern),
                Event.canonical_event_key.ilike(pattern),
                Event.city.ilike(pattern),
                Event.region.ilike(pattern),
            )
        )
    if country_code:
        statement = statement.where(Event.country_code == country_code.upper())
    if status_filter:
        statement = statement.where(Event.status == status_filter)
    if road_type:
        statement = statement.where(Event.road_type == road_type)
    if intersection_type:
        statement = statement.where(Event.intersection_type == intersection_type)
    if weather:
        statement = statement.where(Event.weather == weather)
    if lighting:
        statement = statement.where(Event.lighting == lighting)
    if min_confidence is not None:
        statement = statement.where(Event.overall_confidence >= min_confidence)
    if max_confidence is not None:
        statement = statement.where(Event.overall_confidence <= max_confidence)
    if review_required is not None:
        statement = statement.where(Event.review_required.is_(review_required))
    if has_blocking_errors is not None:
        statement = statement.where(
            Event.blocking_error_count > 0 if has_blocking_errors else Event.blocking_error_count == 0
        )
    if run_id:
        from backend.models.orm import AutomationRun

        run_pk = session.scalar(select(AutomationRun.run_pk).where(AutomationRun.run_id == run_id))
        statement = statement.where(Event.last_run_pk == run_pk)

    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(
        statement.order_by(desc(Event.updated_at)).offset(max(0, offset)).limit(max(1, min(limit, 500)))
    ).all()

    events = []
    for event in rows:
        summary = _summary(event)
        # object_type filtering is done in Python because it is a JSON list.
        if object_type and object_type not in (event.object_types or []):
            continue
        categories = session.scalars(
            select(ValidationResult.category)
            .where(ValidationResult.event_pk == event.event_pk, ValidationResult.passed.is_(False))
            .distinct()
        ).all()
        summary["abnormality_categories"] = sorted(set(categories))
        events.append(summary)

    return {"events": events, "total": total, "limit": limit, "offset": offset}


def _event_or_404(session: Session, key: str) -> Event:
    event = session.scalar(select(Event).where(Event.canonical_event_key == key))
    if event is None:
        event = session.scalar(select(Event).where(Event.anonymized_event_ref == key))
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No event '{key}'.")
    return event


@router.get("/{key}")
def event_detail(key: str, session: DbSession) -> dict[str, Any]:
    """Everything the Event Detail tabs need, in one payload."""
    event = _event_or_404(session, key)
    analysis = event.analysis_json or {}
    summary = _summary(event)
    summary["abnormality_categories"] = sorted(
        set(
            session.scalars(
                select(ValidationResult.category)
                .where(ValidationResult.event_pk == event.event_pk, ValidationResult.passed.is_(False))
                .distinct()
            ).all()
        )
    )

    streams = session.scalars(select(SensorStream).where(SensorStream.event_pk == event.event_pk)).all()
    poses = session.scalars(
        select(EgoPose).where(EgoPose.event_pk == event.event_pk).order_by(EgoPose.t_rel_s)
    ).all()
    features = session.scalars(select(MapFeature).where(MapFeature.event_pk == event.event_pk)).all()
    detections = session.scalars(
        select(Detection).where(Detection.event_pk == event.event_pk).order_by(Detection.t_rel_s)
    ).all()
    recommendations = session.scalars(
        select(FieldRecommendation).where(FieldRecommendation.event_pk == event.event_pk)
    ).all()
    validations = session.scalars(
        select(ValidationResult).where(ValidationResult.event_pk == event.event_pk)
    ).all()
    evidence = session.scalars(select(Evidence).where(Evidence.event_pk == event.event_pk)).all()
    reviews = session.scalars(
        select(Review).where(Review.event_pk == event.event_pk).order_by(Review.reviewed_at)
    ).all()
    audit_entries = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_ref == event.anonymized_event_ref)
        .order_by(desc(AuditEvent.created_at))
        .limit(100)
    ).all()

    return {
        "summary": summary,
        "metadata": event.metadata_json,
        "analysis": analysis,
        "streams": [
            {
                "stream_type": s.stream_type,
                "camera_position": s.camera_position,
                "requirement": s.requirement,
                "sample_count": s.sample_count,
                "expected_sample_count": s.expected_sample_count,
                "availability_status": s.availability_status,
                "availability_pct": s.availability_pct,
                "sync_offset_ms": s.sync_offset_ms,
                "max_gap_ms": s.max_gap_ms,
                "quality_score": s.quality_score,
                "issues": s.issues or [],
            }
            for s in streams
        ],
        "trajectory": [
            {
                "t": p.t_rel_s,
                "x_m": p.x_m,
                "y_m": p.y_m,
                "heading_rad": p.heading_rad,
                "speed_mps": p.speed_mps,
                "arc_length_m": p.arc_length_m,
            }
            for p in poses
        ],
        "map_features": [
            {
                "feature_id": f.feature_id,
                "feature_type": f.feature_type,
                "geometry": f.geometry,
                "attributes": f.topology_attributes,
                "map_version": f.map_version,
                "confidence": f.confidence,
            }
            for f in features
        ],
        "detections": [
            {
                "t": d.t_rel_s,
                "camera": d.camera,
                "source": d.source,
                "object_type": d.object_type,
                "object_subtype": d.object_subtype,
                "track_id": d.track_id,
                "bounding_box": d.bounding_box,
                "state": d.state,
                "distance_m": d.distance_m,
                "velocity_mps": d.velocity_mps,
                "lane_relation": d.lane_relation,
                "confidence": d.confidence,
                "model_version": d.model_version,
            }
            for d in detections
        ],
        "recommendations": [
            {
                "field_name": r.field_name,
                "original_value": (r.original_value_json or {}).get("value"),
                "recommended_value": (r.recommended_value_json or {}).get("value"),
                "alternatives": r.alternatives_json or [],
                "confidence": r.confidence,
                "band": r.confidence_band,
                "explanation": r.confidence_components or {},
                "reason": r.explanation,
                "method": r.method,
                "auto_selected": r.auto_selected,
                "safety_critical": r.safety_critical,
                "status": r.status,
                "model_or_rule_version": r.model_or_rule_version,
            }
            for r in recommendations
        ],
        "validation": [
            {
                "rule_id": v.rule_name,
                "category": v.category,
                "severity": v.severity,
                "passed": v.passed,
                "skipped": v.skipped,
                "skip_reason": v.skip_reason,
                "message": v.message,
                "observed": v.observed_json,
                "recommended_correction": v.recommended_correction,
                "blocks_export": v.blocks_export,
                "requires_review": v.requires_review,
                "rule_version": v.rule_version,
            }
            for v in validations
        ],
        "evidence": [
            {
                "evidence_id": e.evidence_id,
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
            }
            for e in evidence
        ],
        "review_history": [
            {
                "field_name": r.field_name,
                "decision": r.decision,
                "original_recommendation": r.original_recommendation,
                "reviewer_value": r.reviewer_value,
                "override_reason": r.override_reason,
                "comment": r.comment,
                "reviewer": r.reviewer,
                "reviewer_role": r.reviewer_role,
                "is_senior_review": r.is_senior_review,
                "superseded": r.superseded,
                "reviewed_at": r.reviewed_at,
            }
            for r in reviews
        ],
        "audit": [
            {
                "action": a.action,
                "actor": a.actor,
                "actor_role": a.actor_role,
                "detail": a.detail,
                "before": a.before_json,
                "after": a.after_json,
                "created_at": a.created_at,
                "software_version": a.software_version,
                "rule_version": a.rule_version,
            }
            for a in audit_entries
        ],
    }
