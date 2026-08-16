"""Target-junction discovery and ranking.

The engine proposes; the reviewer disposes. Ranking produces a scored list with
an explicit reason per candidate, and the top candidate is only auto-selected
when its confidence clears the configured band *and* it is clearly ahead of the
runner-up. A near-tie is routed to the reviewer instead of being resolved by a
coin flip.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon

from backend.models.contracts import (
    EventMetadata,
    JunctionCandidate,
    MapContext,
    MapFeatureContract,
    Trajectory,
)
from backend.settings import get_settings

#: Field of view (half-angle, degrees) used for the camera-visibility heuristic.
FORWARD_FOV_DEG = 50.0
VISIBILITY_RANGE_M = 200.0

#: Weights are explicit so the "why" panel can show the arithmetic.
WEIGHTS: dict[str, float] = {
    "trajectory_intersects": 0.35,
    "proximity": 0.15,
    "heading_agreement": 0.15,
    "time_agreement": 0.10,
    "road_type_match": 0.10,
    "traffic_control_match": 0.10,
    "camera_visibility": 0.05,
}

#: A candidate must beat the runner-up by this margin to be auto-selected.
AMBIGUITY_MARGIN = 0.08


def _polygon_points(feature: MapFeatureContract) -> list[list[float]]:
    geometry = feature.geometry
    if geometry.type != "Polygon" or not geometry.coordinates:
        return []
    ring = geometry.coordinates[0]
    return [[float(p[0]), float(p[1])] for p in ring if len(p) >= 2]


def _centroid(points: list[list[float]]) -> tuple[float, float] | None:
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def find_candidate_junctions(
    trajectory: Trajectory,
    map_context: MapContext,
    radius_m: float | None = None,
) -> list[MapFeatureContract]:
    """Mapped junctions whose polygon lies within ``radius_m`` of the trajectory."""
    if not trajectory.valid or len(trajectory.points) < 2 or not map_context.available:
        return []
    radius = radius_m if radius_m is not None else float(
        get_settings().section("geometry").get("junction_search_radius_m", 150.0)
    )
    line = LineString([(p.x_m, p.y_m) for p in trajectory.points])

    found: list[MapFeatureContract] = []
    for feature in map_context.by_type("junction"):
        points = _polygon_points(feature)
        if len(points) < 2:
            # Degenerate geometry is still a candidate: the polygon rule must be
            # allowed to fail loudly rather than the junction silently vanishing.
            found.append(feature)
            continue
        try:
            shape = Polygon(points) if len(points) >= 3 else LineString(points)
            if line.distance(shape) <= radius:
                found.append(feature)
        except (ValueError, TypeError):
            found.append(feature)
    return found


def _heading_agreement(trajectory: Trajectory, centroid: tuple[float, float]) -> float | None:
    """1.0 when the junction lies straight ahead at closest approach, 0.0 behind."""
    best = None
    best_distance = float("inf")
    for point in trajectory.points:
        distance = math.hypot(point.x_m - centroid[0], point.y_m - centroid[1])
        if distance < best_distance:
            best_distance, best = distance, point
    if best is None or best.heading_rad is None:
        return None

    # Use a point ~30 m before closest approach so "ahead" is well defined.
    approach_arc = max(0.0, best.arc_length_m - 30.0)
    reference = min(trajectory.points, key=lambda p: abs(p.arc_length_m - approach_arc))
    if reference.heading_rad is None:
        reference = best

    bearing = math.atan2(centroid[1] - reference.y_m, centroid[0] - reference.x_m)
    assert reference.heading_rad is not None
    delta = abs(math.atan2(math.sin(bearing - reference.heading_rad), math.cos(bearing - reference.heading_rad)))
    return round(max(0.0, 1.0 - delta / math.pi), 4)


def _camera_visibility(trajectory: Trajectory, centroid: tuple[float, float]) -> float:
    """Fraction of trajectory samples with the junction in the forward view."""
    usable = [p for p in trajectory.points if p.heading_rad is not None]
    if not usable:
        return 0.0
    fov = math.radians(FORWARD_FOV_DEG)
    visible = 0
    for point in usable:
        distance = math.hypot(centroid[0] - point.x_m, centroid[1] - point.y_m)
        if distance > VISIBILITY_RANGE_M:
            continue
        bearing = math.atan2(centroid[1] - point.y_m, centroid[0] - point.x_m)
        assert point.heading_rad is not None
        delta = abs(math.atan2(math.sin(bearing - point.heading_rad), math.cos(bearing - point.heading_rad)))
        if delta <= fov:
            visible += 1
    return round(visible / len(usable), 4)


def _time_agreement(trajectory: Trajectory, centroid: tuple[float, float], event_t_rel: float | None) -> tuple[float | None, float | None]:
    """Return ``(score, delta_seconds)`` between closest approach and the event time."""
    if event_t_rel is None:
        return None, None
    closest = min(
        trajectory.points,
        key=lambda p: math.hypot(p.x_m - centroid[0], p.y_m - centroid[1]),
    )
    delta = abs(closest.t - event_t_rel)
    # Full credit within 2 s, no credit beyond 20 s.
    score = max(0.0, min(1.0, 1.0 - (delta - 2.0) / 18.0)) if delta > 2.0 else 1.0
    return round(score, 4), round(delta, 3)


def rank_junctions(
    trajectory: Trajectory,
    candidates: list[MapFeatureContract],
    metadata: EventMetadata | None = None,
    event_t_rel: float | None = None,
) -> list[JunctionCandidate]:
    """Score every candidate. Highest score first; ties keep map order."""
    if not trajectory.valid or len(trajectory.points) < 2:
        return []
    line = LineString([(p.x_m, p.y_m) for p in trajectory.points])
    radius = float(get_settings().section("geometry").get("junction_search_radius_m", 150.0))

    scored: list[JunctionCandidate] = []
    for feature in candidates:
        points = _polygon_points(feature)
        centroid = _centroid(points)
        attributes = feature.attributes or {}

        components: dict[str, float] = {}
        reasons: list[str] = []

        # Trajectory intersection.
        intersects = False
        distance: float | None = None
        if len(points) >= 3:
            try:
                shape = Polygon(points)
                intersects = bool(line.intersects(shape))
                distance = round(float(line.distance(shape)), 3)
            except (ValueError, TypeError):
                intersects, distance = False, None
        elif centroid is not None:
            distance = round(float(line.distance(Point(centroid))), 3)

        components["trajectory_intersects"] = 1.0 if intersects else 0.0
        reasons.append(
            "Ego trajectory passes through this junction."
            if intersects
            else (
                "Ego trajectory does not enter this junction"
                + (f" (closest approach {distance:.1f} m)." if distance is not None else ".")
            )
        )

        # Proximity.
        if distance is not None:
            components["proximity"] = round(max(0.0, 1.0 - distance / max(radius, 1.0)), 4)
            if not intersects:
                reasons.append(f"Closest approach is {distance:.1f} m from the route.")

        # Heading.
        if centroid is not None:
            heading_score = _heading_agreement(trajectory, centroid)
            if heading_score is not None:
                components["heading_agreement"] = heading_score
                reasons.append(f"Heading agreement {heading_score * 100:.0f}%.")

            visibility = _camera_visibility(trajectory, centroid)
            components["camera_visibility"] = visibility
            if visibility > 0:
                reasons.append(f"Junction is in the forward camera view for {visibility * 100:.0f}% of the clip.")

            time_score, time_delta = _time_agreement(trajectory, centroid, event_t_rel)
            if time_score is not None:
                components["time_agreement"] = time_score
                reasons.append(f"Closest approach is {time_delta:.1f} s from the reported event time.")

        # Metadata agreement.
        road_match: bool | None = None
        if metadata and metadata.road_type and attributes.get("road_type"):
            road_match = str(attributes["road_type"]).lower() == metadata.road_type.lower()
            components["road_type_match"] = 1.0 if road_match else 0.0
            reasons.append(
                f"Road type {'matches' if road_match else 'differs from'} the event metadata "
                f"({attributes['road_type']} vs {metadata.road_type})."
            )

        control_match: bool | None = None
        if metadata and metadata.traffic_control_entity and attributes.get("traffic_control"):
            mapped = {str(c).lower() for c in attributes["traffic_control"]}
            wanted = {str(c).lower() for c in metadata.traffic_control_entity}
            control_match = bool(mapped & wanted)
            components["traffic_control_match"] = 1.0 if control_match else 0.0
            reasons.append(
                f"Traffic control {'matches' if control_match else 'differs from'} the event metadata."
            )

        # Weighted score over the components that could actually be evaluated:
        # a missing input never silently counts as agreement.
        total_weight = sum(WEIGHTS[k] for k in components)
        score = (
            round(sum(components[k] * WEIGHTS[k] for k in components) / total_weight, 4)
            if total_weight > 0
            else 0.0
        )

        scored.append(
            JunctionCandidate(
                feature_id=feature.feature_id,
                score=score,
                reasons=reasons,
                map_alignment_confidence=float(feature.confidence or 0.0),
                distance_to_trajectory_m=distance,
                trajectory_intersects=intersects,
                heading_agreement=components.get("heading_agreement"),
                time_agreement_s=None,
                road_type_match=road_match,
                traffic_control_match=control_match,
                camera_visibility=components.get("camera_visibility"),
                polygon=points,
                attributes={**attributes, "score_components": components, "weights_used": {k: WEIGHTS[k] for k in components}},
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def selection_is_ambiguous(ranked: list[JunctionCandidate]) -> bool:
    """True when the top two candidates are too close to separate automatically."""
    if len(ranked) < 2:
        return False
    return (ranked[0].score - ranked[1].score) < AMBIGUITY_MARGIN


def intersection_complexity(candidate: JunctionCandidate | None) -> str:
    """Derive complexity from mapped structure only.

    Uses branch count, lane count, traffic-control count and turn options -
    quantities the map actually states. When the map does not state them the
    answer is ``unknown``; no project-specific threshold is invented.
    """
    if candidate is None:
        return "unknown"
    attributes = candidate.attributes or {}
    branches = attributes.get("branch_count")
    lanes = attributes.get("lane_count")
    controls = attributes.get("traffic_control")
    turns = attributes.get("turn_options")

    if branches is None and lanes is None and turns is None:
        return "unknown"

    score = 0
    if isinstance(branches, (int, float)):
        score += max(0, int(branches) - 3)
    if isinstance(lanes, (int, float)):
        score += max(0, (int(lanes) - 1) // 2)
    if isinstance(controls, list):
        score += max(0, len(controls) - 1)
    if isinstance(turns, (int, float)):
        score += max(0, int(turns) - 2)

    if score <= 0:
        return "simple"
    if score <= 2:
        return "moderate"
    if score <= 4:
        return "complex"
    return "very_complex"


def as_polygon(candidate: JunctionCandidate | None) -> Polygon | None:
    if candidate is None or len(candidate.polygon) < 3:
        return None
    try:
        return Polygon(candidate.polygon)
    except (ValueError, TypeError):
        return None


def centroid_of(candidate: JunctionCandidate | None) -> tuple[float, float] | None:
    if candidate is None:
        return None
    return _centroid(candidate.polygon)
