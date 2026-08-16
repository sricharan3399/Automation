"""Junction polygon validation and repair suggestion.

Checks required by the platform spec:

* at least 3 unique points
* points not collinear
* ring is simple (no self-intersection)
* area inside the configured plausible range
* the ego trajectory actually crosses the polygon

When the supplied polygon fails, a *recommended* replacement (convex hull of
the supplied points) is offered - never applied automatically. The reviewer
accepts, edits, redraws or rejects it.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from backend.models.contracts import PolygonAssessment, Trajectory
from backend.settings import get_settings

COLLINEAR_AREA_EPS = 1e-6


def _dedupe(points: list[list[float]], tolerance: float = 1e-6) -> list[list[float]]:
    """Drop consecutive duplicates and any explicit closing point."""
    cleaned: list[list[float]] = []
    for point in points:
        if len(point) < 2:
            continue
        candidate = [float(point[0]), float(point[1])]
        if cleaned and math.dist(cleaned[-1], candidate) <= tolerance:
            continue
        cleaned.append(candidate)
    if len(cleaned) > 1 and math.dist(cleaned[0], cleaned[-1]) <= tolerance:
        cleaned.pop()
    return cleaned


def _is_collinear(points: list[list[float]]) -> bool:
    if len(points) < 3:
        return True
    (x0, y0) = points[0]
    for i in range(1, len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        cross = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
        if abs(cross) > COLLINEAR_AREA_EPS:
            return False
    return True


def _convex_hull(points: list[list[float]]) -> list[list[float]]:
    """Andrew's monotone chain - a defensible, deterministic repair suggestion."""
    pts = sorted({(p[0], p[1]) for p in points})
    if len(pts) < 3:
        return [list(p) for p in pts]

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return [list(p) for p in hull]


def close_ring(points: list[list[float]]) -> list[list[float]]:
    if not points:
        return []
    if math.dist(points[0], points[-1]) > 1e-9:
        return [*points, list(points[0])]
    return list(points)


def assess_polygon(points: list[list[float]], trajectory: Trajectory | None = None) -> PolygonAssessment:
    """Validate a junction polygon and propose a correction when it is unusable."""
    cfg = get_settings().section("geometry")
    min_points = int(cfg.get("min_polygon_points", 3))
    min_area = float(cfg.get("min_junction_area_m2", 25.0))
    max_area = float(cfg.get("max_junction_area_m2", 40000.0))

    existing = [list(map(float, p)) for p in points if len(p) >= 2]
    unique = _dedupe(existing)

    assessment = PolygonAssessment(
        point_count=len(existing),
        unique_point_count=len(unique),
        existing_polygon=close_ring(unique) if unique else [],
    )

    if len(unique) < min_points:
        assessment.issues.append(
            f"Polygon has {len(unique)} unique point(s); at least {min_points} are required."
        )
        assessment.collinear = _is_collinear(unique)
        assessment.confidence = 0.0
        return assessment

    if _is_collinear(unique):
        assessment.collinear = True
        assessment.issues.append("All polygon points are collinear, so the polygon encloses no area.")
        assessment.confidence = 0.0
        return assessment

    ring = close_ring(unique)
    try:
        polygon = Polygon(ring)
    except (ValueError, TypeError) as exc:
        assessment.issues.append(f"Polygon could not be constructed: {exc}")
        assessment.confidence = 0.0
        return assessment

    assessment.is_simple = bool(polygon.exterior.is_simple)
    assessment.is_valid = bool(polygon.is_valid) and assessment.is_simple
    assessment.self_intersecting = not assessment.is_simple
    assessment.area_m2 = round(abs(polygon.area), 3)

    if assessment.self_intersecting:
        assessment.issues.append("The polygon boundary intersects itself.")
    if assessment.area_m2 is not None:
        if assessment.area_m2 < min_area:
            assessment.issues.append(
                f"Area {assessment.area_m2:.1f} m² is below the plausible minimum of {min_area:.0f} m²."
            )
        elif assessment.area_m2 > max_area:
            assessment.issues.append(
                f"Area {assessment.area_m2:.1f} m² exceeds the plausible maximum of {max_area:.0f} m²."
            )

    if trajectory is not None and trajectory.valid and len(trajectory.points) >= 2:
        line = LineString([(p.x_m, p.y_m) for p in trajectory.points])
        try:
            assessment.trajectory_crosses = bool(line.intersects(polygon))
        except Exception:  # pragma: no cover - shapely predicate on degenerate input
            assessment.trajectory_crosses = False
        if not assessment.trajectory_crosses:
            assessment.issues.append("The ego trajectory does not pass through this polygon.")

    # Recommend a repair only when the supplied ring is unusable.
    if not assessment.is_valid:
        hull = _convex_hull(unique)
        if len(hull) >= 3:
            assessment.recommended_polygon = close_ring(hull)

    confidence = 1.0
    if assessment.self_intersecting:
        confidence -= 0.6
    if not assessment.trajectory_crosses and trajectory is not None and trajectory.valid:
        confidence -= 0.3
    if assessment.area_m2 is not None and not (min_area <= assessment.area_m2 <= max_area):
        confidence -= 0.25
    if len(unique) < 4:
        # A triangle is legal but rarely how a real junction is mapped.
        confidence -= 0.1
    assessment.confidence = round(max(0.0, min(1.0, confidence)), 3)

    return assessment


def polygon_edges(points: list[list[float]]) -> list[tuple[int, list[float], list[float]]]:
    """Return ``(index, p1, p2)`` for each edge of the closed ring."""
    ring = _dedupe([list(map(float, p)) for p in points if len(p) >= 2])
    if len(ring) < 3:
        return []
    edges: list[tuple[int, list[float], list[float]]] = []
    for i in range(len(ring)):
        edges.append((i, ring[i], ring[(i + 1) % len(ring)]))
    return edges


def normalised_ring(points: list[list[float]]) -> list[list[float]]:
    """Counter-clockwise, closed ring - stable edge indices across runs."""
    ring = _dedupe([list(map(float, p)) for p in points if len(p) >= 2])
    if len(ring) < 3:
        return close_ring(ring)
    try:
        oriented = orient(Polygon(close_ring(ring)), sign=1.0)
        return [[round(x, 6), round(y, 6)] for x, y in oriented.exterior.coords]
    except (ValueError, TypeError):
        return close_ring(ring)
