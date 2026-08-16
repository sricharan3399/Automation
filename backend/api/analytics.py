"""QA analytics, error breakdown, review quality and performance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.api.deps import DbSession
from backend.models.orm import (
    AutomationRun,
    Event,
    FieldRecommendation,
    Review,
    SensorStream,
    ValidationResult,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(session: DbSession, days: int = 30) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))

    total_events = session.scalar(select(func.count(Event.event_pk))) or 0
    by_status: dict[str, int] = {
        str(status): int(count)
        for status, count in session.execute(
            select(Event.status, func.count(Event.event_pk)).group_by(Event.status)
        ).all()
    }

    runs = session.scalars(select(AutomationRun).where(AutomationRun.created_at >= since)).all()
    processed = sum(r.records_processed for r in runs)
    elapsed = sum(r.elapsed_seconds or 0.0 for r in runs if r.elapsed_seconds)
    events_per_hour = round(processed / (elapsed / 3600.0), 2) if elapsed > 0 else None

    failures = session.scalar(
        select(func.count(ValidationResult.validation_pk)).where(ValidationResult.passed.is_(False))
    ) or 0
    evaluated = session.scalar(
        select(func.count(ValidationResult.validation_pk)).where(ValidationResult.skipped.is_(False))
    ) or 0
    blocking = session.scalar(
        select(func.count(Event.event_pk)).where(Event.blocking_error_count > 0)
    ) or 0

    decisions: dict[str, int] = {
        str(decision): int(count)
        for decision, count in session.execute(
            select(Review.decision, func.count(Review.review_pk)).group_by(Review.decision)
        ).all()
    }
    total_decisions = sum(decisions.values())

    mean_quality = session.scalar(select(func.avg(SensorStream.quality_score))) or None
    mean_confidence = session.scalar(select(func.avg(Event.overall_confidence))) or None
    duplicates = sum(r.duplicates_merged for r in runs)

    return {
        "window_days": days,
        "events_processed": total_events,
        "events_per_hour": events_per_hour,
        "runs": len(runs),
        "by_status": by_status,
        "candidate_error_rate": round(failures / evaluated, 4) if evaluated else None,
        "blocking_data_error_rate": round(blocking / total_events, 4) if total_events else None,
        "review": {
            "decisions": decisions,
            "total": total_decisions,
            "acceptance_rate": round(decisions.get("ACCEPT", 0) / total_decisions, 4) if total_decisions else None,
            "override_rate": round(
                (decisions.get("EDIT", 0) + decisions.get("REJECT", 0)) / total_decisions, 4
            )
            if total_decisions
            else None,
        },
        "duplicates_merged": duplicates,
        "mean_sensor_quality": round(float(mean_quality), 4) if mean_quality is not None else None,
        "mean_overall_confidence": round(float(mean_confidence), 4) if mean_confidence is not None else None,
        "note": (
            "Review outcomes are recorded for rule and threshold improvement. No model is retrained "
            "automatically; that requires an approved governance pipeline."
        ),
    }


@router.get("/error-breakdown")
def error_breakdown(
    session: DbSession,
    country_code: str | None = None,
    road_type: str | None = None,
    weather: str | None = None,
    lighting: str | None = None,
    intersection_type: str | None = None,
    lane_count: int | None = None,
) -> dict[str, Any]:
    """Failure counts by category and rule, filterable by scene dimension."""
    statement = (
        select(ValidationResult.category, ValidationResult.rule_name, ValidationResult.severity, func.count())
        .join(Event, ValidationResult.event_pk == Event.event_pk)
        .where(ValidationResult.passed.is_(False))
        .group_by(ValidationResult.category, ValidationResult.rule_name, ValidationResult.severity)
    )
    if country_code:
        statement = statement.where(Event.country_code == country_code.upper())
    if road_type:
        statement = statement.where(Event.road_type == road_type)
    if weather:
        statement = statement.where(Event.weather == weather)
    if lighting:
        statement = statement.where(Event.lighting == lighting)
    if intersection_type:
        statement = statement.where(Event.intersection_type == intersection_type)
    if lane_count is not None:
        statement = statement.where(Event.lane_count == lane_count)

    rows = session.execute(statement).all()
    by_category: dict[str, int] = {}
    by_rule: list[dict[str, Any]] = []
    for category, rule_name, severity, count in rows:
        by_category[category] = by_category.get(category, 0) + int(count)
        by_rule.append({"rule_id": rule_name, "category": category, "severity": severity, "count": int(count)})

    by_rule.sort(key=lambda item: int(item["count"] or 0), reverse=True)
    return {"by_category": by_category, "by_rule": by_rule, "filters_applied": {
        "country_code": country_code,
        "road_type": road_type,
        "weather": weather,
        "lighting": lighting,
        "intersection_type": intersection_type,
        "lane_count": lane_count,
    }}


@router.get("/review-quality")
def review_quality(session: DbSession) -> dict[str, Any]:
    """How often automation recommendations are accepted, edited or rejected."""
    rows = session.execute(
        select(Review.field_name, Review.decision, func.count(Review.review_pk))
        .group_by(Review.field_name, Review.decision)
    ).all()

    per_field: dict[str, dict[str, int]] = {}
    for field_name, decision, count in rows:
        bucket = per_field.setdefault(field_name, {"ACCEPT": 0, "EDIT": 0, "REJECT": 0})
        bucket[decision] = bucket.get(decision, 0) + int(count)

    summary = []
    for field_name, bucket in sorted(per_field.items()):
        total = sum(bucket.values())
        summary.append(
            {
                "field": field_name,
                **bucket,
                "total": total,
                "acceptance_rate": round(bucket.get("ACCEPT", 0) / total, 4) if total else None,
            }
        )
    summary.sort(key=lambda item: int(item["total"] or 0), reverse=True)

    bands = session.execute(
        select(FieldRecommendation.confidence_band, func.count(FieldRecommendation.recommendation_pk))
        .group_by(FieldRecommendation.confidence_band)
    ).all()

    return {
        "per_field": summary,
        "recommendations_by_band": {band: int(count) for band, count in bands},
    }


@router.get("/performance")
def performance(session: DbSession, limit: int = 20) -> dict[str, Any]:
    runs = session.scalars(
        select(AutomationRun).order_by(AutomationRun.created_at.desc()).limit(max(1, min(limit, 200)))
    ).all()
    series = []
    for run in runs:
        elapsed = run.elapsed_seconds
        series.append(
            {
                "run_id": run.run_id,
                "status": run.status,
                "dry_run": run.dry_run,
                "records_processed": run.records_processed,
                "elapsed_seconds": round(elapsed, 2) if elapsed else None,
                "seconds_per_event": round(elapsed / run.records_processed, 3)
                if elapsed and run.records_processed
                else None,
                "blocking_error_count": run.blocking_error_count,
                "csv_rows_created": run.csv_rows_created,
                "created_at": run.created_at,
            }
        )
    return {"runs": series}


@router.get("/sensor-quality")
def sensor_quality(session: DbSession) -> dict[str, Any]:
    rows = session.execute(
        select(
            SensorStream.stream_type,
            SensorStream.camera_position,
            func.avg(SensorStream.quality_score),
            func.avg(SensorStream.availability_pct),
            func.count(SensorStream.stream_pk),
        ).group_by(SensorStream.stream_type, SensorStream.camera_position)
    ).all()
    return {
        "streams": [
            {
                "stream_type": stream_type,
                "camera_position": camera,
                "mean_quality_score": round(float(quality), 4) if quality is not None else None,
                "mean_availability_pct": round(float(availability), 2) if availability is not None else None,
                "sample_size": int(count),
            }
            for stream_type, camera, quality, availability, count in rows
        ]
    }
