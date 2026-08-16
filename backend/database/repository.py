"""Persistence helpers for review packages, reviews and queries.

The write path is an UPSERT keyed on ``canonical_event_key``. Re-processing an
event refreshes the derived artefacts (streams, poses, detections, map features,
recommendations, validation results, evidence) and bumps ``record_version``.

Reviews are never touched by re-processing. They are the human record, they are
append-only, and the pipeline reads them so that a reviewed field keeps its
decision instead of being quietly replaced by a fresh machine recommendation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.models.contracts import ReviewPackage, Trajectory
from backend.models.orm import (
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

log = logging.getLogger(__name__)

#: Poses are stored decimated: the review UI needs shape, not every sample.
POSE_STORE_STEP = 2


def existing_event_keys(session: Session) -> set[str]:
    return set(session.scalars(select(Event.canonical_event_key)).all())


def event_by_key(session: Session, canonical_event_key: str) -> Event | None:
    return session.scalar(select(Event).where(Event.canonical_event_key == canonical_event_key))


def reviews_for_keys(session: Session, keys: set[str] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    """Latest non-superseded review per (event, field), keyed by canonical key."""
    statement = (
        select(Review, Event.canonical_event_key)
        .join(Event, Review.event_pk == Event.event_pk)
        .where(Review.superseded.is_(False))
        .order_by(Review.reviewed_at)
    )
    if keys:
        statement = statement.where(Event.canonical_event_key.in_(keys))

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for review, key in session.execute(statement).all():
        out.setdefault(key, {})[review.field_name] = {
            "value": (review.reviewer_value or {}).get("value"),
            "decision": review.decision,
            "comment": review.comment,
            "override_reason": review.override_reason,
            "reviewer": review.reviewer,
            "reviewer_role": review.reviewer_role,
            "reviewed_at": review.reviewed_at,
            "is_senior_review": review.is_senior_review,
        }
    return out


def _clear_derived(session: Session, event_pk: int) -> None:
    """Remove derived artefacts before re-inserting. Reviews are excluded."""
    for table in (SensorStream, EgoPose, MapFeature, Detection, FieldRecommendation, ValidationResult, Evidence):
        session.execute(delete(table).where(table.event_pk == event_pk))


def upsert_review_package(
    session: Session,
    package: ReviewPackage,
    run_pk: int | None,
    trajectory: Trajectory | None = None,
    bundle: Any | None = None,
) -> tuple[Event, bool]:
    """Insert or update the event and all its derived artefacts.

    Returns ``(event, was_duplicate)``.
    """
    metadata = package.metadata
    existing = event_by_key(session, package.canonical_event_key)
    was_duplicate = existing is not None

    event = existing or Event(canonical_event_key=package.canonical_event_key)
    if existing is None:
        event.first_run_pk = run_pk
        event.record_version = 1
    else:
        event.record_version = (existing.record_version or 1) + 1

    package.record_version = event.record_version

    event.anonymized_job_ref = package.anonymized_job_ref
    event.anonymized_session_ref = package.anonymized_session_ref
    event.anonymized_event_ref = package.anonymized_event_ref
    event.source_event_id = metadata.event_id
    event.source_session_id = metadata.session_id
    event.event_type = metadata.event_type
    event.approx_event_time = metadata.event_time
    event.evaluation_start = metadata.evaluation_start
    event.evaluation_end = metadata.evaluation_end
    event.country = metadata.country
    event.country_code = metadata.country_code
    event.country_source_field = metadata.country_source_field
    event.region = metadata.region
    event.city = metadata.city
    event.road_type = metadata.road_type
    event.lane_count = metadata.lane_count
    event.lane_relation = next(
        (r.recommended_value for r in package.recommendations if r.field_name == "lane_relation"), None
    )
    event.intersection_type = metadata.intersection_type
    event.intersection_complexity = metadata.intersection_complexity
    event.weather = metadata.weather
    event.lighting = metadata.lighting
    event.object_types = list(metadata.object_type)
    event.bus_type = metadata.bus_type
    event.scenario_tags = sorted(set(metadata.scenario_tags) | set(package.scene.detected_scenario_tags))
    event.metadata_json = metadata.model_dump(mode="json")
    event.analysis_json = {
        "synchronization": package.synchronization.model_dump(mode="json"),
        "geometry": package.geometry.model_dump(mode="json"),
        "scene": package.scene.model_dump(mode="json"),
        "behavior": package.behavior.model_dump(mode="json"),
        "trajectory_summary": package.trajectory_summary,
        "automation_recommendation": package.automation_recommendation,
        "is_synthetic": package.is_synthetic,
    }
    event.overall_confidence = package.overall_confidence
    event.blocking_error_count = package.blocking_error_count
    event.review_required = package.review_required
    event.last_run_pk = run_pk
    event.updated_at = datetime.now(timezone.utc)

    # A human decision outranks a re-run: do not downgrade a reviewed record
    # back to a machine status.
    if event.status not in ("CONFIRMED_BY_TESTER", "REJECTED_BY_TESTER"):
        event.status = package.status.value

    session.add(event)
    session.flush()

    _clear_derived(session, event.event_pk)

    # --- streams --------------------------------------------------------
    for health in package.synchronization.stream_health:
        session.add(
            SensorStream(
                event_pk=event.event_pk,
                stream_type=health.stream_type,
                camera_position=health.camera_position,
                requirement=health.requirement.value,
                sample_count=health.sample_count,
                expected_sample_count=health.expected_sample_count,
                availability_status=health.status,
                availability_pct=health.availability_pct,
                sync_offset_ms=health.sync_offset_ms,
                max_gap_ms=health.max_gap_ms,
                quality_score=health.quality_score,
                issues=list(health.issues),
            )
        )

    # --- poses ----------------------------------------------------------
    if trajectory is not None and trajectory.points:
        for point in trajectory.points[::POSE_STORE_STEP]:
            session.add(
                EgoPose(
                    event_pk=event.event_pk,
                    t_rel_s=point.t,
                    x_m=point.x_m,
                    y_m=point.y_m,
                    heading_rad=point.heading_rad,
                    speed_mps=point.speed_mps,
                    arc_length_m=point.arc_length_m,
                    localization_quality=trajectory.localization_quality,
                )
            )

    # --- map features ----------------------------------------------------
    if bundle is not None and getattr(bundle, "map_context", None) is not None:
        context = bundle.map_context
        if context.available:
            for feature in context.features:
                session.add(
                    MapFeature(
                        event_pk=event.event_pk,
                        feature_id=feature.feature_id,
                        feature_type=feature.feature_type,
                        geometry=feature.geometry.model_dump(mode="json"),
                        topology_attributes=dict(feature.attributes),
                        map_version=feature.map_version,
                        confidence=feature.confidence,
                    )
                )

        for detection in getattr(bundle, "detections", []) or []:
            session.add(
                Detection(
                    event_pk=event.event_pk,
                    t_rel_s=detection.t,
                    camera=detection.camera,
                    source=detection.source,
                    object_type=detection.object_type,
                    object_subtype=detection.object_subtype,
                    track_id=detection.track_id,
                    bounding_box=dict(detection.bounding_box),
                    state=detection.state,
                    distance_m=detection.distance_m,
                    velocity_mps=detection.velocity_mps,
                    lane_relation=detection.lane_relation,
                    confidence=detection.confidence,
                    model_version=detection.model_version,
                )
            )

    # --- recommendations --------------------------------------------------
    for recommendation in package.recommendations:
        session.add(
            FieldRecommendation(
                event_pk=event.event_pk,
                field_name=recommendation.field_name,
                original_value_json={"value": recommendation.original_value},
                recommended_value_json={"value": recommendation.recommended_value},
                alternatives_json=list(recommendation.alternatives),
                confidence=recommendation.confidence,
                confidence_band=recommendation.band.value,
                confidence_components=recommendation.explanation.model_dump(mode="json"),
                method=recommendation.method,
                explanation=recommendation.reason,
                auto_selected=recommendation.auto_selected,
                safety_critical=recommendation.safety_critical,
                status=recommendation.status.value,
                model_or_rule_version=recommendation.model_or_rule_version,
            )
        )

    # --- validation results ------------------------------------------------
    for outcome in package.validation.outcomes:
        session.add(
            ValidationResult(
                event_pk=event.event_pk,
                rule_name=outcome.rule_id,
                category=outcome.category,
                severity=outcome.severity.value,
                passed=outcome.passed,
                skipped=outcome.skipped,
                skip_reason=outcome.skip_reason,
                message=outcome.message,
                observed_json=dict(outcome.observed),
                recommended_correction=outcome.recommended_correction,
                blocks_export=outcome.blocks_export,
                requires_review=outcome.requires_review,
                rule_version=outcome.rule_version,
            )
        )

    # --- evidence ------------------------------------------------------------
    for item in package.evidence:
        session.add(
            Evidence(
                event_pk=event.event_pk,
                evidence_id=item.evidence_id,
                purpose=item.purpose,
                camera=item.camera,
                t_rel_s=item.t_rel_s,
                kind=item.kind,
                relative_path=item.relative_path,
                content_hash=item.content_hash,
                redacted=item.redacted,
                redaction_report=dict(item.redaction_report),
                approved=item.approved,
                available=item.available,
                unavailable_reason=item.unavailable_reason,
            )
        )

    session.flush()
    return event, was_duplicate


# ---------------------------------------------------------------------------
# Query helpers used by the API
# ---------------------------------------------------------------------------
def event_counts_by_status(session: Session, run_pk: int | None = None) -> dict[str, int]:
    statement = select(Event.status, func.count(Event.event_pk)).group_by(Event.status)
    if run_pk is not None:
        statement = statement.where(Event.last_run_pk == run_pk)
    return {str(status): int(count) for status, count in session.execute(statement).all()}


def packages_for_run(session: Session, run_pk: int) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(Event.last_run_pk == run_pk).order_by(Event.event_pk)
        ).all()
    )


def supersede_reviews(session: Session, event_pk: int, field_name: str) -> None:
    """Mark previous decisions on a field as superseded.

    Nothing is deleted: the full decision history stays queryable from the
    Review History and Audit pages.
    """
    for review in session.scalars(
        select(Review).where(
            Review.event_pk == event_pk,
            Review.field_name == field_name,
            Review.superseded.is_(False),
        )
    ).all():
        review.superseded = True
        session.add(review)
