"""Scene understanding.

Consumes the detections supplied by the source (perception results and, where
available, reference annotations) and produces the scene-level facts the rest
of the pipeline needs:

* track summaries for the target object class
* ``first_visible`` and ``full_view`` instants
* observed traffic-light states
* scenario tags actually evidenced by the data

Note on scope: when no approved on-board detector is configured, this module
does not invent detections from raw video. It analyses what the source
provides, and reports ``available=False`` with a reason when the source
provides nothing. Running an approved model is a separate, explicitly enabled
step (see docs/DATA_SCOUT_INTEGRATION.md).
"""

from __future__ import annotations

from typing import Any

from backend.models.contracts import (
    DetectionContract,
    SceneAnalysis,
    SceneFinding,
    TrackSummary,
)
from backend.settings import get_settings
from backend.vision.geometry2d import fully_in_view, merge_intervals
from backend.vision.perception_errors import analyse_perception
from backend.vision.tracking_errors import analyse_tracking


def _cfg() -> dict[str, Any]:
    return get_settings().section("perception")


def summarise_tracks(detections: list[DetectionContract], object_type: str | None = None) -> list[TrackSummary]:
    grouped: dict[str, list[DetectionContract]] = {}
    for detection in detections:
        if detection.source != "perception" or not detection.track_id:
            continue
        if object_type and detection.object_type != object_type:
            continue
        grouped.setdefault(detection.track_id, []).append(detection)

    summaries: list[TrackSummary] = []
    for track_id, items in sorted(grouped.items()):
        items.sort(key=lambda d: d.t)
        times = [d.t for d in items]
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        confidences = [d.confidence for d in items if d.confidence is not None]
        summaries.append(
            TrackSummary(
                track_id=track_id,
                object_type=items[0].object_type,
                first_t=round(times[0], 3),
                last_t=round(times[-1], 3),
                sample_count=len(items),
                cameras=sorted({d.camera for d in items if d.camera}),
                mean_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
                max_gap_s=round(max(gaps), 3) if gaps else None,
                lane_relations=sorted({d.lane_relation for d in items if d.lane_relation}),
            )
        )
    return summaries


def _first_visible_and_full_view(
    detections: list[DetectionContract], object_type: str
) -> tuple[float | None, float | None]:
    """First detection of the target, and first instant it is wholly in view.

    ``full_view`` requires the box to be entirely inside the image with a
    configured margin *and* to stay that way for at least two consecutive
    detections, so a single jittery frame does not define the marker.
    """
    margin = float(_cfg().get("full_view_margin", 0.02))
    candidates = sorted(
        (d for d in detections if d.source == "perception" and d.object_type == object_type),
        key=lambda d: d.t,
    )
    if not candidates:
        return None, None

    first_visible = candidates[0].t
    full_view: float | None = None
    streak_start: float | None = None
    streak = 0
    for detection in candidates:
        if fully_in_view(detection.bounding_box, margin):
            if streak == 0:
                streak_start = detection.t
            streak += 1
            if streak >= 2:
                full_view = streak_start
                break
        else:
            streak = 0
            streak_start = None

    return round(first_visible, 3), (round(full_view, 3) if full_view is not None else None)


def _traffic_light_observations(detections: list[DetectionContract]) -> list[dict[str, Any]]:
    """Collapse per-frame signal detections into observed state intervals."""
    signals = sorted(
        (d for d in detections if d.object_type == "traffic_light" and d.source == "perception"),
        key=lambda d: d.t,
    )
    observations: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for detection in signals:
        state = detection.state or "unknown"
        if current is not None and current["state"] == state:
            current["t_end"] = round(detection.t, 3)
            current["sample_count"] += 1
            current["confidences"].append(detection.confidence or 0.0)
            continue
        if current is not None:
            observations.append(current)
        current = {
            "state": state,
            "t_start": round(detection.t, 3),
            "t_end": round(detection.t, 3),
            "sample_count": 1,
            "confidences": [detection.confidence or 0.0],
            "track_id": detection.track_id,
        }
    if current is not None:
        observations.append(current)

    for observation in observations:
        confidences = observation.pop("confidences")
        observation["mean_confidence"] = round(sum(confidences) / len(confidences), 4) if confidences else None
    return observations


def _detected_scenario_tags(
    detections: list[DetectionContract],
    tracks: list[TrackSummary],
    object_type: str,
) -> list[str]:
    """Scenario tags the detection data actually evidences.

    Deliberately conservative: a tag is emitted only where the data supports it
    directly. Tags that need map or behaviour context are added later by the
    behaviour engine, not guessed here.
    """
    tags: set[str] = set()
    targets = [d for d in detections if d.source == "perception" and d.object_type == object_type]
    if not targets:
        return []

    relations = {d.lane_relation for d in targets if d.lane_relation}
    if "object_in_ego_lane" in relations or "object_ahead" in relations:
        tags.add("BUS_AHEAD_SAME_LANE")
    if relations & {"object_left_adjacent", "object_right_adjacent"}:
        tags.add("BUS_ADJACENT_LANE")
    if "object_crossing_ego_path" in relations:
        tags.add("BUS_CROSSING_EGO_PATH")
    if "object_at_bus_stop" in relations:
        tags.add("BUS_AT_STOP")

    distances = [d.distance_m for d in targets if d.distance_m is not None]
    if distances and min(distances) <= 10.0:
        tags.add("BUS_CLOSE_DISTANCE")

    margin = float(_cfg().get("full_view_margin", 0.02))
    if any(not fully_in_view(d.bounding_box, margin) for d in targets):
        tags.add("BUS_PARTIALLY_VISIBLE")

    if any(t.object_subtype == "articulated_bus" for t in targets if t.object_subtype):
        tags.add("ARTICULATED_BUS")

    # Concurrent distinct tracks of the target class -> more than one bus.
    if len(tracks) >= 2:
        for a in tracks:
            for b in tracks:
                if a.track_id < b.track_id and a.first_t <= b.last_t and b.first_t <= a.last_t:
                    tags.add("MULTIPLE_BUSES")
                    break

    if any(d.object_type == "traffic_light" for d in detections):
        tags.add("BUS_SIGNAL_INTERACTION")
    if any(d.object_type == "pedestrian" for d in detections):
        tags.add("BUS_PEDESTRIAN_INTERACTION")
    if any(d.object_type == "bicycle" for d in detections):
        tags.add("BUS_CYCLIST_INTERACTION")

    # Sustained absence between detections of a still-tracked object.
    gap_tolerance = float(get_settings().section("tracking").get("max_track_gap_s", 0.5))
    intervals = merge_intervals([d.t for d in targets], gap_tolerance)
    if len(intervals) > 1:
        tags.add("BUS_OCCLUDED")

    return sorted(tags)


def analyse_scene(
    detections: list[DetectionContract],
    reference_data_available: bool,
    target_object_type: str | None = None,
) -> SceneAnalysis:
    """Run the full scene, perception and tracking analysis for one event."""
    object_type = target_object_type or str(_cfg().get("target_object_type", "bus"))

    if not detections:
        return SceneAnalysis(
            available=False,
            unavailable_reason=(
                "The source supplied no detections for this event, so perception, tracking and "
                "object-visibility analysis could not run. Connect a perception result source or "
                "enable the approved detection step."
            ),
            reference_data_available=reference_data_available,
            target_object_type=object_type,
        )

    tracks = summarise_tracks(detections, object_type)
    first_visible, full_view = _first_visible_and_full_view(detections, object_type)

    perception_findings: list[SceneFinding] = analyse_perception(
        detections, reference_data_available, object_type
    )
    tracking_findings: list[SceneFinding] = analyse_tracking(detections, tracks, object_type)

    target_detections = [d for d in detections if d.source == "perception" and d.object_type == object_type]
    confidences = [d.confidence for d in target_detections if d.confidence is not None]

    return SceneAnalysis(
        available=True,
        reference_data_available=reference_data_available,
        detection_count=len(detections),
        tracks=tracks,
        target_object_type=object_type,
        first_visible_t=first_visible,
        full_view_t=full_view,
        perception_findings=perception_findings,
        tracking_findings=tracking_findings,
        traffic_light_observations=_traffic_light_observations(detections),
        detected_scenario_tags=_detected_scenario_tags(detections, tracks, object_type),
        mean_detection_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
        model_versions=sorted({d.model_version for d in detections if d.model_version}),
    )
