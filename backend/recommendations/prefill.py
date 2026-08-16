"""Auto-prefill engine.

Turns the analysis results into per-field recommendations, each with its own
confidence, explanation and routing status.

Two invariants:

* **Prior reviewer decisions are never overwritten.** A field a human has
  already decided keeps that decision; the machine recommendation is still
  computed and shown side by side, but it is not auto-selected.
* **Safety-critical disagreement escalates.** When automation disagrees with the
  existing value on a field marked safety-critical, the record is routed to
  senior review regardless of how confident the machine is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.models.contracts import (
    ConfidenceBand,
    FieldRecommendationContract,
    RecordStatus,
    TimestampMarker,
)
from backend.recommendations.confidence import (
    band_for,
    compute_field_confidence,
    is_safety_critical,
    may_auto_select,
)
from backend.validation.registry import ValidationContext
from backend.version import METHOD_VERSION

#: Which confidence field governs each recommendation field.
CONFIDENCE_FIELD: dict[str, str] = {
    "target_junction": "junction_confidence",
    "junction_polygon": "polygon_confidence",
    "entry_edge": "entry_edge_confidence",
    "exit_edge": "exit_edge_confidence",
    "first_visible_time": "first_seen_confidence",
    "full_view_time": "first_seen_confidence",
    "traffic_light_state": "traffic_light_state_confidence",
    "signal_relevance": "signal_relevance_confidence",
    "intersection_complexity": "map_alignment_confidence",
    "lane_relation": "bus_detection_confidence",
    "stop_classification": "behavior_confidence",
    "vehicle_maneuver": "behavior_confidence",
    "bus_type": "bus_detection_confidence",
}

TIMESTAMP_FIELDS = {
    "timestamp_200m",
    "timestamp_100m",
    "timestamp_60m",
    "wait_line_crossing_time",
    "junction_entry_time",
    "junction_exit_time",
    "post_junction_20m_time",
}

MARKER_TO_FIELD = {
    "timestamp_200m": "timestamp_200m",
    "timestamp_100m": "timestamp_100m",
    "timestamp_60m": "timestamp_60m",
    "wait_line_crossing": "wait_line_crossing_time",
    "junction_entry": "junction_entry_time",
    "junction_exit": "junction_exit_time",
    "post_junction_20m": "post_junction_20m_time",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _cross_camera_agreement(ctx: ValidationContext) -> float | None:
    """Agreement across cameras that observed the target object.

    Returns ``None`` when only one camera saw it - a single view cannot
    corroborate itself, and pretending otherwise would inflate confidence.
    """
    cameras: set[str] = set()
    for track in ctx.scene.tracks:
        cameras.update(track.cameras)
    if len(cameras) < 2:
        return None
    per_camera_counts: dict[str, int] = {}
    for detection in ctx.bundle.detections:
        if detection.source == "perception" and detection.camera and detection.object_type == ctx.scene.target_object_type:
            per_camera_counts[detection.camera] = per_camera_counts.get(detection.camera, 0) + 1
    if len(per_camera_counts) < 2:
        return None
    counts = sorted(per_camera_counts.values())
    return round(counts[0] / counts[-1], 4)


def _sensor_quality(ctx: ValidationContext) -> float:
    return ctx.sync.confidence


def _map_agreement(ctx: ValidationContext) -> float | None:
    if not ctx.bundle.map_context.available:
        return None
    return ctx.geometry.map_alignment_confidence or None


def _recommendation(
    field_name: str,
    *,
    original: Any,
    recommended: Any,
    components: dict[str, float | None],
    reason: str,
    ctx: ValidationContext,
    alternatives: list[dict[str, Any]] | None = None,
    method: str = "analytic",
    existing_reviews: dict[str, Any] | None = None,
) -> FieldRecommendationContract:
    confidence, explanation = compute_field_confidence(field_name, components)
    band = band_for(confidence)
    safety_critical = is_safety_critical(CONFIDENCE_FIELD.get(field_name, field_name))

    reviewed = bool(existing_reviews and field_name in existing_reviews)
    disagrees = (
        original not in (None, "", [], {})
        and recommended not in (None, "", [], {})
        and _jsonable(original) != _jsonable(recommended)
    )

    auto_selected = may_auto_select(confidence) and not reviewed
    if band == ConfidenceBand.MANUAL:
        recommended = None
        auto_selected = False

    if reviewed:
        status = RecordStatus.CONFIRMED_BY_TESTER
        reason = (
            f"{reason} A reviewer has already decided this field; the existing decision is preserved "
            "and this recommendation is shown for comparison only."
        )
    elif safety_critical and disagrees:
        status = RecordStatus.SENIOR_REVIEW_REQUIRED
        reason = (
            f"{reason} Automation disagrees with the recorded value on a safety-critical field, "
            "so senior review is required regardless of confidence."
        )
        auto_selected = False
    elif band == ConfidenceBand.AUTO_CONFIRM:
        status = RecordStatus.AUTO_PREPARED
    else:
        status = RecordStatus.REVIEW_REQUIRED

    return FieldRecommendationContract(
        field_name=field_name,
        original_value=_jsonable(original),
        recommended_value=_jsonable(recommended),
        alternatives=alternatives or [],
        confidence=confidence,
        band=band,
        explanation=explanation,
        reason=reason,
        method=method,
        auto_selected=auto_selected,
        safety_critical=safety_critical,
        status=status,
        model_or_rule_version=f"{METHOD_VERSION}/{ctx.sync.contract_version}",
    )


def _timestamp_recommendation(
    marker: TimestampMarker,
    ctx: ValidationContext,
    existing_reviews: dict[str, Any] | None,
) -> FieldRecommendationContract | None:
    field_name = MARKER_TO_FIELD.get(marker.name)
    if field_name is None:
        return None

    if not marker.available:
        return _recommendation(
            field_name,
            original=None,
            recommended=None,
            components={"model_confidence": None, "sensor_quality": _sensor_quality(ctx)},
            reason=marker.unavailable_reason or "This marker could not be derived.",
            ctx=ctx,
            method=marker.method,
            existing_reviews=existing_reviews,
        )

    # Temporal stability for a derived timestamp is the inverse of its
    # interpolation resolution: a 0.05 s resolution is far more trustworthy
    # than a 0.5 s one.
    error = marker.interpolation_error_s or 0.0
    stability = round(max(0.0, 1.0 - min(error / 0.5, 1.0)), 4)

    return _recommendation(
        field_name,
        original=None,
        recommended=marker.absolute or marker.t,
        components={
            "model_confidence": marker.confidence,
            "map_agreement": marker.map_confidence,
            "temporal_stability": stability,
            "sensor_quality": _sensor_quality(ctx),
        },
        reason=(
            f"Derived at t={marker.t:.3f}s by {marker.method.replace('_', ' ')}"
            + (f" at {marker.distance_m:.0f} m." if marker.distance_m else ".")
            + f" Interpolation resolution ±{error:.3f}s."
        ),
        ctx=ctx,
        method=marker.method,
        existing_reviews=existing_reviews,
    )


def build_recommendations(
    ctx: ValidationContext,
    existing_reviews: dict[str, Any] | None = None,
) -> list[FieldRecommendationContract]:
    """Produce a recommendation for every field the pipeline can populate."""
    reviews = existing_reviews or {}
    out: list[FieldRecommendationContract] = []
    cross_camera = _cross_camera_agreement(ctx)
    sensor_quality = _sensor_quality(ctx)
    map_agreement = _map_agreement(ctx)

    # --- target junction --------------------------------------------------
    junction = ctx.geometry.target_junction
    alternatives = [
        {
            "value": alt.feature_id,
            "score": alt.score,
            "reasons": alt.reasons,
            "distance_m": alt.distance_to_trajectory_m,
        }
        for alt in ctx.geometry.alternatives[:3]
    ]
    if junction is not None:
        ambiguous = bool(ctx.extras.get("junction_ambiguous"))
        out.append(
            _recommendation(
                "target_junction",
                original=ctx.metadata.unmapped.get("target_junction"),
                recommended=junction.feature_id,
                components={
                    "model_confidence": junction.score,
                    "map_agreement": junction.map_alignment_confidence or None,
                    "temporal_stability": 1.0 if junction.trajectory_intersects else 0.2,
                    "sensor_quality": sensor_quality,
                    "cross_camera_agreement": junction.camera_visibility,
                },
                reason=(
                    " ".join(junction.reasons)
                    + (
                        " The top two candidates score too closely to separate automatically, "
                        "so this selection needs review."
                        if ambiguous
                        else ""
                    )
                ),
                ctx=ctx,
                alternatives=alternatives,
                method="junction_ranking",
                existing_reviews=reviews,
            )
        )
    else:
        out.append(
            _recommendation(
                "target_junction",
                original=None,
                recommended=None,
                components={"model_confidence": None, "sensor_quality": sensor_quality},
                reason=ctx.geometry.unavailable_reason or "No junction candidate could be ranked.",
                ctx=ctx,
                method="junction_ranking",
                existing_reviews=reviews,
            )
        )

    # --- polygon ----------------------------------------------------------
    polygon = ctx.geometry.polygon
    polygon_alternatives = (
        [{"value": polygon.recommended_polygon, "label": "convex hull of supplied points"}]
        if polygon.recommended_polygon
        else []
    )
    out.append(
        _recommendation(
            "junction_polygon",
            original=polygon.existing_polygon or None,
            recommended=(polygon.recommended_polygon or polygon.existing_polygon) or None,
            components={
                "model_confidence": polygon.confidence,
                "map_agreement": map_agreement,
                "temporal_stability": 1.0 if polygon.trajectory_crosses else 0.2,
                "sensor_quality": sensor_quality,
            },
            reason=(
                "Polygon is valid and the trajectory crosses it."
                if polygon.is_valid and polygon.trajectory_crosses
                else " ".join(polygon.issues) or "Polygon could not be assessed."
            ),
            ctx=ctx,
            alternatives=polygon_alternatives,
            method="polygon_assessment",
            existing_reviews=reviews,
        )
    )

    # --- entry / exit edges ------------------------------------------------
    for field_name, edge, alts in (
        ("entry_edge", ctx.geometry.entry_edge, ctx.geometry.entry_alternatives),
        ("exit_edge", ctx.geometry.exit_edge, ctx.geometry.exit_alternatives),
    ):
        out.append(
            _recommendation(
                field_name,
                original=None,
                recommended=edge.edge_id if edge else None,
                components={
                    "model_confidence": edge.confidence if edge else None,
                    "map_agreement": map_agreement,
                    "temporal_stability": 1.0 if edge and edge.crossing_t is not None else None,
                    "sensor_quality": sensor_quality,
                },
                reason=(
                    " ".join(edge.reasons)
                    if edge
                    else "The trajectory produced no boundary crossing for this edge."
                ),
                ctx=ctx,
                alternatives=[
                    {"value": a.edge_id, "confidence": a.confidence, "reasons": a.reasons} for a in alts
                ],
                method="boundary_transition",
                existing_reviews=reviews,
            )
        )

    # --- object visibility -------------------------------------------------
    for field_name, value in (
        ("first_visible_time", ctx.scene.first_visible_t),
        ("full_view_time", ctx.scene.full_view_t),
    ):
        out.append(
            _recommendation(
                field_name,
                original=None,
                recommended=value,
                components={
                    "model_confidence": ctx.scene.mean_detection_confidence,
                    "cross_camera_agreement": cross_camera,
                    "temporal_stability": 1.0 if value is not None else None,
                    "sensor_quality": sensor_quality,
                },
                reason=(
                    f"Determined from the {ctx.scene.target_object_type} detection sequence at "
                    f"t={value:.3f}s."
                    if value is not None
                    else (
                        ctx.scene.unavailable_reason
                        or f"No {ctx.scene.target_object_type} detection sequence established this instant."
                    )
                ),
                ctx=ctx,
                method="scene_analysis",
                existing_reviews=reviews,
            )
        )

    # --- derived timestamps ------------------------------------------------
    for marker in ctx.geometry.markers:
        recommendation = _timestamp_recommendation(marker, ctx, reviews)
        if recommendation is not None:
            out.append(recommendation)

    # --- traffic light state ------------------------------------------------
    observations = ctx.scene.traffic_light_observations
    if observations:
        dominant = max(observations, key=lambda o: o["sample_count"])
        total = sum(o["sample_count"] for o in observations)
        stability = round(dominant["sample_count"] / total, 4) if total else None
        out.append(
            _recommendation(
                "traffic_light_state",
                original=ctx.metadata.traffic_light_state,
                recommended=dominant["state"],
                components={
                    "model_confidence": dominant.get("mean_confidence"),
                    "cross_camera_agreement": cross_camera,
                    "temporal_stability": stability,
                    "sensor_quality": sensor_quality,
                },
                reason=(
                    f"State '{dominant['state']}' observed for {dominant['sample_count']} of {total} "
                    f"signal samples ({dominant['t_start']:.2f}s - {dominant['t_end']:.2f}s)."
                ),
                ctx=ctx,
                alternatives=[
                    {"value": o["state"], "samples": o["sample_count"], "t_start": o["t_start"]}
                    for o in observations
                    if o is not dominant
                ],
                method="signal_state_aggregation",
                existing_reviews=reviews,
            )
        )
        # Signal relevance: whether the observed signal governs the ego lane.
        mapped_signals = (
            ctx.bundle.map_context.by_type("traffic_signal") if ctx.bundle.map_context.available else []
        )
        governs = [f for f in mapped_signals if str(f.attributes.get("controls_lane", "")).lower() == "ego"]
        out.append(
            _recommendation(
                "signal_relevance",
                original=None,
                recommended="governs_ego_lane" if governs else "unconfirmed",
                components={
                    "model_confidence": (governs[0].confidence if governs else None),
                    "map_agreement": map_agreement,
                    "sensor_quality": sensor_quality,
                },
                reason=(
                    f"{len(governs)} mapped signal(s) are attributed to the ego lane."
                    if governs
                    else "No mapped signal is attributed to the ego lane, so relevance is unconfirmed."
                ),
                ctx=ctx,
                method="map_attribution",
                existing_reviews=reviews,
            )
        )

    # --- scene / behaviour classifications ----------------------------------
    out.append(
        _recommendation(
            "intersection_complexity",
            original=ctx.metadata.intersection_complexity,
            recommended=ctx.extras.get("intersection_complexity"),
            components={
                "model_confidence": (junction.map_alignment_confidence if junction else None),
                "map_agreement": map_agreement,
                "sensor_quality": sensor_quality,
            },
            reason=(
                "Derived from mapped branch count, lane count, traffic-control count and turn "
                "options. 'unknown' when the map does not state them."
            ),
            ctx=ctx,
            method="map_structure",
            existing_reviews=reviews,
        )
    )

    lane_relations = sorted({r for track in ctx.scene.tracks for r in track.lane_relations})
    out.append(
        _recommendation(
            "lane_relation",
            original=None,
            recommended=lane_relations[0] if lane_relations else None,
            components={
                "model_confidence": ctx.scene.mean_detection_confidence,
                "cross_camera_agreement": cross_camera,
                "temporal_stability": 1.0 if len(lane_relations) == 1 else (0.5 if lane_relations else None),
                "sensor_quality": sensor_quality,
            },
            reason=(
                f"Detections reported lane relation(s): {', '.join(lane_relations)}."
                if lane_relations
                else "No lane relation was reported on any detection."
            ),
            ctx=ctx,
            alternatives=[{"value": r} for r in lane_relations[1:]],
            method="detection_attribute",
            existing_reviews=reviews,
        )
    )

    out.append(
        _recommendation(
            "stop_classification",
            original=None,
            recommended=ctx.behavior.stop_classification if ctx.behavior.available else None,
            components={
                "model_confidence": ctx.behavior.confidence if ctx.behavior.available else None,
                "temporal_stability": ctx.behavior.confidence if ctx.behavior.available else None,
                "sensor_quality": sensor_quality,
            },
            reason=(
                " ".join(
                    o.observation for o in ctx.behavior.observations if o.name in ("minimum_speed", "below_stop_threshold")
                )
                + " This is a classification of the measurement, not a verdict on the vehicle."
                if ctx.behavior.available
                else (ctx.behavior.unavailable_reason or "Behaviour analysis did not run.")
            ),
            ctx=ctx,
            method="behavior_analysis",
            existing_reviews=reviews,
        )
    )

    out.append(
        _recommendation(
            "vehicle_maneuver",
            original=ctx.metadata.vehicle_maneuver,
            recommended=ctx.behavior.maneuver if ctx.behavior.available else None,
            components={
                "model_confidence": ctx.behavior.confidence if ctx.behavior.available else None,
                "map_agreement": map_agreement,
                "temporal_stability": ctx.behavior.confidence if ctx.behavior.available else None,
                "sensor_quality": sensor_quality,
            },
            reason=(
                f"Net heading change {ctx.behavior.heading_change_deg:.1f}° over the clip."
                if ctx.behavior.available and ctx.behavior.heading_change_deg is not None
                else (ctx.behavior.unavailable_reason or "Behaviour analysis did not run.")
            ),
            ctx=ctx,
            method="behavior_analysis",
            existing_reviews=reviews,
        )
    )

    subtypes = sorted(
        {d.object_subtype for d in ctx.bundle.detections if d.object_subtype and d.object_type == ctx.scene.target_object_type}
    )
    out.append(
        _recommendation(
            "bus_type",
            original=ctx.metadata.bus_type,
            recommended=subtypes[0] if subtypes else ctx.metadata.bus_type,
            components={
                "model_confidence": ctx.scene.mean_detection_confidence,
                "cross_camera_agreement": cross_camera,
                "temporal_stability": 1.0 if len(subtypes) == 1 else (0.5 if subtypes else None),
                "sensor_quality": sensor_quality,
            },
            reason=(
                f"Detections reported subtype(s): {', '.join(subtypes)}."
                if subtypes
                else "No detection reported an object subtype; the metadata value is carried through."
            ),
            ctx=ctx,
            alternatives=[{"value": s} for s in subtypes[1:]],
            method="detection_attribute",
            existing_reviews=reviews,
        )
    )

    return out


def field_confidence_map(recommendations: list[FieldRecommendationContract]) -> dict[str, float]:
    """The named confidence fields, for the CSV and the confidence explanation UI."""
    out: dict[str, float] = {}
    for recommendation in recommendations:
        key = CONFIDENCE_FIELD.get(recommendation.field_name)
        if recommendation.field_name in TIMESTAMP_FIELDS:
            key = "timestamp_confidence"
        if key is None:
            continue
        # Keep the weakest value seen for a shared confidence field.
        out[key] = min(out.get(key, 1.0), recommendation.confidence)
    return out
