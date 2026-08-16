"""Human review queue and reviewer decisions.

The review queue is where machine candidates become human decisions. Two rules
are enforced here rather than left to the UI:

* A high-severity or safety-critical override requires a non-empty reason of at
  least the configured length. There is no way to record such an override
  without one.
* Approving a safety-critical override requires the senior-tester permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from backend.api.deps import DbSession, require
from backend.audit.logger import ACTION_REVIEW_DECISION, ACTION_REVIEW_OVERRIDE, audit
from backend.auth.identity import Identity
from backend.auth.roles import Permission, has_permission
from backend.configstore import get_config_store
from backend.database.repository import supersede_reviews
from backend.models.contracts import RecordStatus
from backend.models.orm import Event, FieldRecommendation, Review, ValidationResult
from backend.recommendations.confidence import describe_policy
from backend.settings import get_settings
from backend.version import METHOD_VERSION

router = APIRouter(prefix="/review", tags=["review"])

@dataclass(frozen=True)
class QueueSpec:
    """How a review tab narrows the event table."""

    kind: str  # all | confidence | blocking | status
    min_confidence: float = 0.0
    max_confidence: float = 1.01
    status: str | None = None


QUEUES: dict[str, QueueSpec] = {
    "all": QueueSpec(kind="all"),
    "high_confidence": QueueSpec(kind="confidence", min_confidence=0.95, max_confidence=1.01),
    "medium_confidence": QueueSpec(kind="confidence", min_confidence=0.80, max_confidence=0.95),
    "low_confidence": QueueSpec(kind="confidence", min_confidence=0.0, max_confidence=0.80),
    "blocking_errors": QueueSpec(kind="blocking"),
    "safety_review": QueueSpec(kind="status", status=RecordStatus.SENIOR_REVIEW_REQUIRED.value),
    "data_issues": QueueSpec(kind="status", status=RecordStatus.BLOCKED_DATA_ERROR.value),
    "completed": QueueSpec(kind="status", status=RecordStatus.CONFIRMED_BY_TESTER.value),
    "rejected": QueueSpec(kind="status", status=RecordStatus.REJECTED_BY_TESTER.value),
}


class ReviewDecision(BaseModel):
    field_name: str
    decision: str = Field(pattern="^(ACCEPT|REJECT|EDIT)$")
    value: Any = None
    override_reason: str | None = None
    comment: str | None = None


class BulkDecision(BaseModel):
    canonical_event_key: str
    decisions: list[ReviewDecision]
    finalize: bool = False
    final_status: str | None = None


@router.get("/queues")
def queues(session: DbSession) -> dict[str, Any]:
    """Counts per review tab."""
    counts: dict[str, int] = {}
    for name in QUEUES:
        statement = _queue_statement(name)
        counts[name] = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    return {
        "queues": counts,
        "confidence_policy": describe_policy(),
        "override_policy": {
            "require_reason_for_severities": get_settings()
            .section("review")
            .get("require_reason_for_override_severities", []),
            "min_reason_chars": get_settings().section("review").get("min_override_reason_chars", 15),
            "senior_review_categories": get_settings().section("review").get("senior_review_categories", []),
        },
    }


def _queue_statement(queue: str):
    statement = select(Event)
    spec = QUEUES.get(queue)
    if spec is None or spec.kind == "all":
        return statement
    if spec.kind == "confidence":
        return statement.where(
            Event.overall_confidence >= spec.min_confidence,
            Event.overall_confidence < spec.max_confidence,
            Event.review_required.is_(True),
        )
    if spec.kind == "blocking":
        return statement.where(Event.blocking_error_count > 0)
    if spec.kind == "status":
        return statement.where(Event.status == spec.status)
    return statement


@router.get("/queue/{queue}")
def queue(
    queue: str,
    session: DbSession,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if queue not in QUEUES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown queue '{queue}'. Available: {', '.join(QUEUES)}",
        )
    statement = _queue_statement(queue)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    events = session.scalars(
        statement.order_by(Event.overall_confidence.asc().nullsfirst(), desc(Event.updated_at))
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "queue": queue,
        "total": total,
        "items": [
            {
                "canonical_event_key": e.canonical_event_key,
                "event_reference": e.anonymized_event_ref,
                "status": e.status,
                "country_code": e.country_code,
                "city": e.city,
                "road_type": e.road_type,
                "intersection_type": e.intersection_type,
                "overall_confidence": e.overall_confidence,
                "blocking_error_count": e.blocking_error_count,
                "review_required": e.review_required,
                "automation_recommendation": (e.analysis_json or {}).get("automation_recommendation"),
                "updated_at": e.updated_at,
            }
            for e in events
        ],
    }


@router.get("/{canonical_event_key}")
def review_detail(canonical_event_key: str, session: DbSession) -> dict[str, Any]:
    """Side-by-side comparison payload for one event."""
    event = session.scalar(select(Event).where(Event.canonical_event_key == canonical_event_key))
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such event.")

    recommendations = session.scalars(
        select(FieldRecommendation).where(FieldRecommendation.event_pk == event.event_pk)
    ).all()
    reviews = {
        r.field_name: r
        for r in session.scalars(
            select(Review).where(Review.event_pk == event.event_pk, Review.superseded.is_(False))
        ).all()
    }
    failures = session.scalars(
        select(ValidationResult).where(
            ValidationResult.event_pk == event.event_pk, ValidationResult.passed.is_(False)
        )
    ).all()

    rows = []
    for recommendation in recommendations:
        review = reviews.get(recommendation.field_name)
        rows.append(
            {
                "field": recommendation.field_name,
                "original": (recommendation.original_value_json or {}).get("value"),
                "recommended": (recommendation.recommended_value_json or {}).get("value"),
                "alternatives": recommendation.alternatives_json or [],
                "confidence": recommendation.confidence,
                "band": recommendation.confidence_band,
                "explanation": recommendation.confidence_components or {},
                "reason": recommendation.explanation,
                "auto_selected": recommendation.auto_selected,
                "safety_critical": recommendation.safety_critical,
                "status": recommendation.status,
                "validation": [
                    {"rule_id": f.rule_name, "severity": f.severity, "message": f.message}
                    for f in failures
                    if recommendation.field_name in (f.observed_json or {}).get("field", recommendation.field_name)
                    or f.category.lower() in recommendation.field_name.lower()
                ],
                "reviewer_value": (review.reviewer_value or {}).get("value") if review else None,
                "reviewer_decision": review.decision if review else None,
                "reviewer": review.reviewer if review else None,
                "reviewed_at": review.reviewed_at if review else None,
            }
        )

    return {
        "canonical_event_key": canonical_event_key,
        "event_reference": event.anonymized_event_ref,
        "status": event.status,
        "overall_confidence": event.overall_confidence,
        "blocking_error_count": event.blocking_error_count,
        "automation_recommendation": (event.analysis_json or {}).get("automation_recommendation"),
        "fields": rows,
        "failures": [
            {
                "rule_id": f.rule_name,
                "category": f.category,
                "severity": f.severity,
                "message": f.message,
                "recommended_correction": f.recommended_correction,
                "blocks_export": f.blocks_export,
            }
            for f in failures
        ],
    }


def _requires_reason(session: Any, event: Event, decision: ReviewDecision, recommendation: FieldRecommendation | None) -> bool:
    if decision.decision == "ACCEPT":
        return False
    settings = get_settings().section("review")
    severities = {str(s).upper() for s in settings.get("require_reason_for_override_severities", [])}
    if recommendation is not None and recommendation.safety_critical:
        return True
    if event.blocking_error_count > 0 and "BLOCKING" in severities:
        return True
    failed = session.scalars(
        select(ValidationResult.severity).where(
            ValidationResult.event_pk == event.event_pk, ValidationResult.passed.is_(False)
        )
    ).all()
    return bool(severities & {str(s).upper() for s in failed})


@router.post("/{canonical_event_key}/decisions")
def submit_decisions(
    canonical_event_key: str,
    payload: BulkDecision,
    session: DbSession,
    identity: Identity = require(Permission.REVIEW),
) -> dict[str, Any]:
    event = session.scalar(select(Event).where(Event.canonical_event_key == canonical_event_key))
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such event.")

    min_chars = int(get_settings().section("review").get("min_override_reason_chars", 15))
    recommendations = {
        r.field_name: r
        for r in session.scalars(
            select(FieldRecommendation).where(FieldRecommendation.event_pk == event.event_pk)
        ).all()
    }

    recorded: list[dict[str, Any]] = []
    for decision in payload.decisions:
        recommendation = recommendations.get(decision.field_name)

        if recommendation is not None and recommendation.safety_critical and decision.decision != "ACCEPT":
            if not has_permission(identity.role, Permission.APPROVE_SAFETY_OVERRIDE):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"'{decision.field_name}' is safety-critical. Overriding it requires the "
                        "senior tester role."
                    ),
                )

        if _requires_reason(session, event, decision, recommendation):
            reason = (decision.override_reason or "").strip()
            if len(reason) < min_chars:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"An override reason of at least {min_chars} characters is required for "
                        f"'{decision.field_name}'. Record why the automated recommendation was not accepted."
                    ),
                )

        supersede_reviews(session, event.event_pk, decision.field_name)

        original = (recommendation.recommended_value_json if recommendation else {}) or {}
        value = decision.value
        if decision.decision == "ACCEPT" and value is None and recommendation is not None:
            value = (recommendation.recommended_value_json or {}).get("value")

        review = Review(
            event_pk=event.event_pk,
            field_name=decision.field_name,
            original_recommendation=original,
            reviewer_value={"value": value},
            decision=decision.decision,
            override_reason=decision.override_reason,
            comment=decision.comment,
            reviewer=identity.user,
            reviewer_role=identity.role.value,
            is_senior_review=has_permission(identity.role, Permission.APPROVE_SAFETY_OVERRIDE)
            and bool(recommendation and recommendation.safety_critical),
            model_version=METHOD_VERSION,
            rule_version=get_config_store().rule_version_signature(),
            reviewed_at=datetime.now(timezone.utc),
        )
        session.add(review)

        audit(
            session,
            ACTION_REVIEW_OVERRIDE if decision.decision != "ACCEPT" else ACTION_REVIEW_DECISION,
            actor=identity.user,
            actor_role=identity.role.value,
            entity_type="event.field",
            entity_ref=f"{event.anonymized_event_ref}:{decision.field_name}",
            before={"recommended": original.get("value"), "confidence": recommendation.confidence if recommendation else None},
            after={"decision": decision.decision, "value": value, "reason": decision.override_reason},
            detail=decision.comment,
        )
        recorded.append({"field": decision.field_name, "decision": decision.decision, "value": value})

    if payload.finalize:
        requested = payload.final_status or RecordStatus.CONFIRMED_BY_TESTER.value
        if requested not in (
            RecordStatus.CONFIRMED_BY_TESTER.value,
            RecordStatus.REJECTED_BY_TESTER.value,
            RecordStatus.SENIOR_REVIEW_REQUIRED.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "A reviewer may only finalise a record as CONFIRMED_BY_TESTER, "
                    "REJECTED_BY_TESTER or SENIOR_REVIEW_REQUIRED."
                ),
            )
        if requested == RecordStatus.CONFIRMED_BY_TESTER.value and event.blocking_error_count > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This record still has {event.blocking_error_count} blocking error(s). "
                    "Resolve them before confirming."
                ),
            )
        before_status = event.status
        event.status = requested
        event.review_required = requested == RecordStatus.SENIOR_REVIEW_REQUIRED.value
        session.add(event)
        audit(
            session,
            ACTION_REVIEW_DECISION,
            actor=identity.user,
            actor_role=identity.role.value,
            entity_type="event",
            entity_ref=event.anonymized_event_ref,
            before={"status": before_status},
            after={"status": requested},
            detail="Record finalised by reviewer.",
        )

    session.flush()
    return {
        "canonical_event_key": canonical_event_key,
        "recorded": recorded,
        "status": event.status,
    }
