"""Entry / exit edge determination.

The ego's inside/outside state is evaluated along the trajectory; each
outside→inside transition is an entry crossing and each inside→outside
transition is an exit crossing. The polygon edge nearest the transition point
is the nominated edge, and alternatives are returned ranked so the reviewer can
switch without re-deriving anything.

An existing, previously reviewed edge is never overwritten here - this module
only proposes. The caller decides, and :mod:`backend.recommendations.prefill`
preserves prior reviewer decisions.
"""

from __future__ import annotations

import math

from shapely.geometry import Point, Polygon

from backend.geometry.polygon import normalised_ring, polygon_edges
from backend.models.contracts import EdgeCandidate, Trajectory, TrajectoryPoint


def _point_to_segment_distance(px: float, py: float, p1: list[float], p2: list[float]) -> float:
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _crossing_angle_factor(direction: tuple[float, float], p1: list[float], p2: list[float]) -> float:
    """1.0 for a perpendicular crossing, →0 for a grazing one."""
    ex, ey = p2[0] - p1[0], p2[1] - p1[1]
    edge_len = math.hypot(ex, ey)
    dir_len = math.hypot(*direction)
    if edge_len == 0 or dir_len == 0:
        return 0.0
    cross = abs(direction[0] * ey - direction[1] * ex) / (edge_len * dir_len)
    return round(min(1.0, cross), 4)


def _transitions(trajectory: Trajectory, polygon: Polygon) -> list[tuple[str, TrajectoryPoint, TrajectoryPoint]]:
    """Ordered ``(kind, before, after)`` transitions across the polygon boundary."""
    states: list[bool] = []
    for point in trajectory.points:
        try:
            states.append(bool(polygon.contains(Point(point.x_m, point.y_m))))
        except Exception:  # pragma: no cover - degenerate geometry
            states.append(False)

    out: list[tuple[str, TrajectoryPoint, TrajectoryPoint]] = []
    for i in range(1, len(states)):
        if states[i] and not states[i - 1]:
            out.append(("entry", trajectory.points[i - 1], trajectory.points[i]))
        elif states[i - 1] and not states[i]:
            out.append(("exit", trajectory.points[i - 1], trajectory.points[i]))
    return out


def _build_candidates(
    before: TrajectoryPoint,
    after: TrajectoryPoint,
    edges: list[tuple[int, list[float], list[float]]],
    feature_id: str,
    multiple_transitions: bool,
) -> list[EdgeCandidate]:
    mid_x = (before.x_m + after.x_m) / 2.0
    mid_y = (before.y_m + after.y_m) / 2.0
    direction = (after.x_m - before.x_m, after.y_m - before.y_m)
    crossing_t = (before.t + after.t) / 2.0

    scored: list[tuple[float, EdgeCandidate]] = []
    for index, p1, p2 in edges:
        distance = _point_to_segment_distance(mid_x, mid_y, p1, p2)
        angle_factor = _crossing_angle_factor(direction, p1, p2)
        # Proximity dominates; a grazing crossing is penalised but not excluded.
        proximity = 1.0 / (1.0 + distance)
        confidence = proximity * (0.6 + 0.4 * angle_factor)
        if multiple_transitions:
            confidence *= 0.85

        reasons = [
            f"Trajectory crosses the boundary {distance:.2f} m from this edge.",
            f"Crossing angle factor {angle_factor:.2f} (1.0 = perpendicular).",
        ]
        if multiple_transitions:
            reasons.append("The trajectory crosses the polygon boundary more than twice; confidence reduced.")

        scored.append(
            (
                distance,
                EdgeCandidate(
                    edge_id=f"{feature_id}#E{index}",
                    p1=p1,
                    p2=p2,
                    confidence=round(max(0.0, min(1.0, confidence)), 4),
                    crossing_t=round(crossing_t, 4),
                    reasons=reasons,
                ),
            )
        )

    scored.sort(key=lambda item: item[0])
    return [candidate for _, candidate in scored]


def rank_edges(
    trajectory: Trajectory,
    polygon_points: list[list[float]],
    feature_id: str,
) -> tuple[EdgeCandidate | None, EdgeCandidate | None, list[EdgeCandidate], list[EdgeCandidate]]:
    """Return ``(entry, exit, entry_alternatives, exit_alternatives)``.

    Any element may be ``None``/empty when the trajectory does not produce the
    corresponding transition; the caller reports that as unavailable rather
    than guessing an edge.
    """
    if not trajectory.valid or len(trajectory.points) < 2 or len(polygon_points) < 3:
        return None, None, [], []

    ring = normalised_ring(polygon_points)
    edges = polygon_edges(ring)
    if not edges:
        return None, None, [], []
    try:
        polygon = Polygon(ring)
        if not polygon.is_valid:
            return None, None, [], []
    except (ValueError, TypeError):
        return None, None, [], []

    transitions = _transitions(trajectory, polygon)
    if not transitions:
        return None, None, [], []

    multiple = len(transitions) > 2
    entry_ranked: list[EdgeCandidate] = []
    exit_ranked: list[EdgeCandidate] = []

    for kind, before, after in transitions:
        candidates = _build_candidates(before, after, edges, feature_id, multiple)
        if kind == "entry" and not entry_ranked:
            entry_ranked = candidates
        elif kind == "exit":
            # The last exit wins: the trajectory has finally left the junction.
            exit_ranked = candidates

    entry = entry_ranked[0] if entry_ranked else None
    exit_edge = exit_ranked[0] if exit_ranked else None

    # An entry and exit resolving to the same edge is implausible topology.
    if entry and exit_edge and entry.edge_id == exit_edge.edge_id:
        entry.reasons.append("Entry and exit resolved to the same edge; topology needs review.")
        exit_edge.reasons.append("Entry and exit resolved to the same edge; topology needs review.")
        entry.confidence = round(entry.confidence * 0.5, 4)
        exit_edge.confidence = round(exit_edge.confidence * 0.5, 4)

    return entry, exit_edge, entry_ranked[1:4], exit_ranked[1:4]


def entry_exit_arc_lengths(
    trajectory: Trajectory,
    polygon_points: list[list[float]],
) -> tuple[float | None, float | None]:
    """Arc length at which the ego enters and leaves the junction."""
    if not trajectory.valid or len(polygon_points) < 3:
        return None, None
    try:
        polygon = Polygon(normalised_ring(polygon_points))
        if not polygon.is_valid:
            return None, None
    except (ValueError, TypeError):
        return None, None

    transitions = _transitions(trajectory, polygon)
    entry_s: float | None = None
    exit_s: float | None = None
    for kind, before, after in transitions:
        midpoint = (before.arc_length_m + after.arc_length_m) / 2.0
        if kind == "entry" and entry_s is None:
            entry_s = midpoint
        elif kind == "exit":
            exit_s = midpoint
    return entry_s, exit_s
