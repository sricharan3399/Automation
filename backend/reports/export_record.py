"""Flattening a review package into one export record.

The *effective* value of a field follows a strict precedence:

    1. a reviewer decision, if one exists
    2. the machine recommendation, but only when its confidence permitted
       auto-selection
    3. blank

Step 3 is the important one. A low-confidence recommendation is shown to the
reviewer in the dashboard, but it is never written into an exported cell. An
exported value therefore always means "a human accepted this, or the machine
was confident enough that the policy allows it" - never "the machine's best
guess, unlabelled".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.models.contracts import (
    FieldRecommendationContract,
    RecordStatus,
    ReviewPackage,
    StreamRequirement,
)

#: A reviewer decision record as stored by the review API.
ReviewRecord = dict[str, Any]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _recommendation_map(package: ReviewPackage) -> dict[str, FieldRecommendationContract]:
    return {r.field_name: r for r in package.recommendations}


def effective_value(
    field_name: str,
    recommendations: dict[str, FieldRecommendationContract],
    reviews: dict[str, ReviewRecord],
) -> Any:
    review = reviews.get(field_name)
    if review is not None:
        if review.get("decision") == "REJECT":
            return None
        return review.get("value")
    recommendation = recommendations.get(field_name)
    if recommendation is None:
        return None
    return recommendation.recommended_value if recommendation.auto_selected else None


def _confidence(field_name: str, recommendations: dict[str, FieldRecommendationContract]) -> float | None:
    recommendation = recommendations.get(field_name)
    return recommendation.confidence if recommendation else None


def _issue_streams(package: ReviewPackage, issue: str) -> list[str]:
    return [h.key for h in package.synchronization.stream_health if issue in h.issues]


def _geometry_validation_result(package: ReviewPackage) -> str:
    geometry_failures = [
        o for o in package.validation.failures if o.category in ("GEOMETRY", "MAP")
    ]
    if not package.geometry.available:
        return "not_evaluated"
    if not geometry_failures:
        return "passed"
    if any(o.severity.value == "BLOCKING" for o in geometry_failures):
        return "blocking"
    return "failed"


def _map_alignment_result(package: ReviewPackage) -> str:
    offset = package.geometry.map_alignment_offset_m
    if offset is None:
        return "not_evaluated"
    failed = any(o.rule_id == "GEOMETRY_MAP_ALIGNMENT" and not o.passed for o in package.validation.outcomes)
    return "out_of_tolerance" if failed else "within_tolerance"


def build_export_row(
    package: ReviewPackage,
    reviews: dict[str, ReviewRecord] | None = None,
    evidence_folder: str | None = None,
    evidence_manifest: str | None = None,
) -> dict[str, Any]:
    """Build the flat record that every CSV template selects columns from."""
    reviews = reviews or {}
    recommendations = _recommendation_map(package)
    metadata = package.metadata
    behavior = package.behavior
    sync = package.synchronization

    decisions = [r.get("decision") for r in reviews.values()]
    override_count = sum(1 for d in decisions if d in ("EDIT", "REJECT"))
    comments = [str(r.get("comment")) for r in reviews.values() if r.get("comment")]
    review_dates = [
        r["reviewed_at"] for r in reviews.values() if isinstance(r.get("reviewed_at"), datetime)
    ]

    availabilities = [
        h.availability_pct
        for h in sync.stream_health
        if h.availability_pct is not None and h.requirement != StreamRequirement.IGNORE
    ]
    offsets = [abs(h.sync_offset_ms) for h in sync.stream_health if h.sync_offset_ms is not None]
    gaps = [h.max_gap_ms for h in sync.stream_health if h.max_gap_ms is not None]

    missing_sensor_warnings = sorted(
        {
            f"{h.key}:{issue}"
            for h in sync.stream_health
            for issue in h.issues
            if issue.startswith("MISSING") or issue.startswith("DROPPED") or issue.startswith("FROZEN")
        }
    )
    data_quality_warnings = sorted(
        {
            f"{o.rule_id}"
            for o in package.validation.failures
            if o.category in ("DATA_QUALITY", "SENSOR", "SYNCHRONIZATION", "LOCALIZATION", "METADATA")
        }
    )

    entry = package.geometry.entry_edge
    exit_edge = package.geometry.exit_edge

    row: dict[str, Any] = {
        # identity / provenance
        "canonical_event_key": package.canonical_event_key,
        "anonymized_job_ref": package.anonymized_job_ref,
        "anonymized_event_ref": package.anonymized_event_ref,
        "anonymized_session_ref": package.anonymized_session_ref,
        "record_version": package.record_version,
        "review_date": _iso(max(review_dates)) if review_dates else None,
        # geography
        "country": metadata.country,
        "country_code": metadata.country_code,
        "region": metadata.region,
        "city": metadata.city,
        "event_time": _iso(metadata.event_time),
        "weather": metadata.weather,
        "lighting": metadata.lighting,
        # scenario
        "scenario_type": metadata.event_type,
        "object_type": metadata.object_type or [package.scene.target_object_type],
        "bus_type": effective_value("bus_type", recommendations, reviews) or metadata.bus_type,
        "scenario_tags": sorted(set(metadata.scenario_tags) | set(package.scene.detected_scenario_tags)),
        # road / lanes
        "road_type": metadata.road_type,
        "lane_count": metadata.lane_count,
        "lane_relation": effective_value("lane_relation", recommendations, reviews),
        # intersection
        "intersection_type": metadata.intersection_type,
        "intersection_complexity": effective_value("intersection_complexity", recommendations, reviews)
        or metadata.intersection_complexity,
        "target_junction": effective_value("target_junction", recommendations, reviews),
        "junction_confidence": _confidence("target_junction", recommendations),
        # traffic control
        "traffic_control_entity": metadata.traffic_control_entity,
        "traffic_light_state": effective_value("traffic_light_state", recommendations, reviews),
        "traffic_light_detection_confidence": _confidence("traffic_light_state", recommendations),
        "traffic_light_state_confidence": _confidence("traffic_light_state", recommendations),
        "signal_relevance_confidence": _confidence("signal_relevance", recommendations),
        # behaviour
        "vehicle_maneuver": effective_value("vehicle_maneuver", recommendations, reviews),
        "approach_detected": behavior.approach_detected if behavior.available else None,
        "deceleration_detected": behavior.deceleration_detected if behavior.available else None,
        "stop_classification": effective_value("stop_classification", recommendations, reviews),
        "minimum_speed": behavior.minimum_speed_mps,
        "stop_duration": behavior.stop_duration_s,
        "wait_line_distance": behavior.wait_line_distance_m,
        # timestamps
        "first_visible_time": effective_value("first_visible_time", recommendations, reviews),
        "full_view_time": effective_value("full_view_time", recommendations, reviews),
        "timestamp_200m": effective_value("timestamp_200m", recommendations, reviews),
        "timestamp_100m": effective_value("timestamp_100m", recommendations, reviews),
        "timestamp_60m": effective_value("timestamp_60m", recommendations, reviews),
        "wait_line_crossing_time": effective_value("wait_line_crossing_time", recommendations, reviews),
        "junction_entry_time": effective_value("junction_entry_time", recommendations, reviews),
        "junction_exit_time": effective_value("junction_exit_time", recommendations, reviews),
        "post_junction_20m_time": effective_value("post_junction_20m_time", recommendations, reviews),
        # geometry
        "polygon_point_count": package.geometry.polygon.unique_point_count,
        "polygon_area_m2": package.geometry.polygon.area_m2,
        "polygon_confidence": _confidence("junction_polygon", recommendations),
        "geometry_validation_result": _geometry_validation_result(package),
        "entry_edge": effective_value("entry_edge", recommendations, reviews) or (entry.edge_id if entry else None),
        "entry_edge_confidence": _confidence("entry_edge", recommendations),
        "exit_edge": effective_value("exit_edge", recommendations, reviews) or (exit_edge.edge_id if exit_edge else None),
        "exit_edge_confidence": _confidence("exit_edge", recommendations),
        "map_alignment_result": _map_alignment_result(package),
        "map_alignment_confidence": package.geometry.map_alignment_confidence,
        "max_lateral_offset_m": package.geometry.map_alignment_offset_m,
        "map_version": package.map_version,
        # sensors / sync
        "required_streams": [h.key for h in sync.stream_health if h.requirement == StreamRequirement.REQUIRED],
        "stream_availability_min_pct": min(availabilities) if availabilities else None,
        "max_sync_offset_ms": max(offsets) if offsets else None,
        "max_timestamp_gap_ms": max(gaps) if gaps else None,
        "frozen_streams": _issue_streams(package, "FROZEN_VIDEO") + _issue_streams(package, "FROZEN_STREAM"),
        "dropped_frame_streams": _issue_streams(package, "DROPPED_FRAMES"),
        "duplicate_frame_streams": _issue_streams(package, "DUPLICATE_FRAMES")
        + _issue_streams(package, "DUPLICATE_SAMPLES"),
        "synchronization_quality": sync.quality,
        "synchronization_confidence": sync.confidence,
        "missing_sensor_warnings": missing_sensor_warnings,
        "data_quality_warnings": data_quality_warnings,
        # perception / tracking
        "perception_findings": sorted({f.code for f in package.scene.perception_findings}),
        "tracking_findings": sorted({f.code for f in package.scene.tracking_findings}),
        "track_count": len(package.scene.tracks),
        "reference_data_available": package.scene.reference_data_available,
        "bus_detection_confidence": package.scene.mean_detection_confidence,
        # automation summary
        "abnormality_categories": package.abnormality_categories,
        "automation_overall_confidence": package.overall_confidence,
        "automation_recommendation": package.automation_recommendation,
        # review
        "reviewer_decision": _reviewer_decision(package.status, decisions),
        "reviewer_override_count": override_count,
        "reviewer_comments": " | ".join(comments) if comments else None,
        # evidence
        "evidence_folder": evidence_folder,
        "evidence_manifest": evidence_manifest,
        # status / versions
        "final_status": package.status.value,
        "submission_blocking_error_count": package.blocking_error_count,
        "model_version": package.model_version,
        "rule_version": package.rule_version,
        "last_updated_at": _iso(datetime.now().astimezone()),
    }
    return row


def _reviewer_decision(status: RecordStatus, decisions: list[Any]) -> str | None:
    if not decisions:
        return None
    if status == RecordStatus.REJECTED_BY_TESTER:
        return "REJECTED"
    if status == RecordStatus.CONFIRMED_BY_TESTER:
        return "CONFIRMED"
    if "EDIT" in decisions:
        return "EDITED"
    if "ACCEPT" in decisions:
        return "ACCEPTED"
    return "PENDING"
