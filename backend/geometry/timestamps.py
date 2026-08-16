"""Timestamp automation.

Derives the approach/junction markers from trajectory arc length:

    200 m / 100 m / 60 m before junction entry
    wait-line crossing
    junction entry / junction exit
    20 m past the junction

Every marker reports how it was produced, the interpolation error inherent in
the estimate, the pose quality at that instant and the map confidence of the
geometry it was measured against. A marker that falls outside the recorded data
is returned ``available=False`` with a reason - the engine never extrapolates
past the end of the clip to satisfy a template column.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from shapely.geometry import LineString

from backend.geometry.edges import entry_exit_arc_lengths
from backend.geometry.trajectory import time_at_arc_length
from backend.models.contracts import (
    MapContext,
    TimestampMarker,
    Trajectory,
)
from backend.settings import get_settings

FIRST_VISIBLE = "first_visible"
FULL_VIEW = "full_view"
WAIT_LINE = "wait_line_crossing"
JUNCTION_ENTRY = "junction_entry"
JUNCTION_EXIT = "junction_exit"
POST_JUNCTION = "post_junction_20m"


def _absolute(origin: datetime | None, t: float | None) -> datetime | None:
    if origin is None or t is None:
        return None
    return origin + timedelta(seconds=t)


#: Canonical display / validation order for the markers.
MARKER_ORDER = [
    FIRST_VISIBLE,
    FULL_VIEW,
    "timestamp_200m",
    "timestamp_100m",
    "timestamp_60m",
    WAIT_LINE,
    JUNCTION_ENTRY,
    JUNCTION_EXIT,
    POST_JUNCTION,
]


def _marker(
    name: str,
    *,
    trajectory: Trajectory,
    arc_length: float | None,
    origin: datetime | None,
    map_confidence: float | None,
    distance_m: float | None = None,
    method: str = "arc_length_interpolation",
    unavailable_reason: str | None = None,
) -> TimestampMarker:
    if arc_length is None:
        return TimestampMarker(
            name=name,
            method=method,
            map_confidence=map_confidence,
            available=False,
            unavailable_reason=unavailable_reason or "The required geometry was not available.",
            confidence=0.0,
        )

    t, interpolation_error = time_at_arc_length(trajectory, arc_length)
    if t is None:
        return TimestampMarker(
            name=name,
            distance_m=distance_m,
            method=method,
            map_confidence=map_confidence,
            available=False,
            unavailable_reason=(
                f"Arc length {arc_length:.1f} m lies outside the recorded trajectory "
                f"(0 - {trajectory.total_length_m:.1f} m). The clip does not cover this marker."
            ),
            confidence=0.0,
        )

    pose_quality = trajectory.localization_quality

    # Confidence: localisation quality, discounted by interpolation resolution
    # and by how much the map geometry itself can be trusted.
    confidence = pose_quality if pose_quality is not None else 0.6
    confidence *= max(0.5, 1.0 - min(interpolation_error, 0.5))
    if map_confidence is not None:
        confidence *= max(0.4, map_confidence)

    return TimestampMarker(
        name=name,
        t=round(t, 4),
        absolute=_absolute(origin, t),
        distance_m=round(distance_m, 2) if distance_m is not None else None,
        method=method,
        interpolation_error_s=round(interpolation_error, 4),
        pose_quality=round(pose_quality, 4) if pose_quality is not None else None,
        map_confidence=round(map_confidence, 4) if map_confidence is not None else None,
        confidence=round(max(0.0, min(1.0, confidence)), 4),
        available=True,
    )


def _wait_line_arc_length(trajectory: Trajectory, map_context: MapContext) -> tuple[float | None, float | None, str | None]:
    """Arc length where the ego crosses the mapped stop/wait line."""
    stop_lines = map_context.by_type("stop_line") if map_context.available else []
    if not stop_lines:
        return None, None, "No stop/wait line is present in the map context for this junction."

    line = LineString([(p.x_m, p.y_m) for p in trajectory.points])
    best_arc: float | None = None
    best_confidence: float | None = None

    for feature in stop_lines:
        coords = feature.geometry.coordinates
        if feature.geometry.type != "LineString" or len(coords) < 2:
            continue
        try:
            stop_line = LineString([(float(c[0]), float(c[1])) for c in coords])
            intersection = line.intersection(stop_line)
        except (ValueError, TypeError):
            continue
        if intersection.is_empty:
            continue
        point = intersection if intersection.geom_type == "Point" else intersection.centroid
        arc = float(line.project(point))
        if best_arc is None or arc < best_arc:
            best_arc = arc
            best_confidence = feature.confidence

    if best_arc is None:
        return None, None, "The ego trajectory does not cross any mapped stop/wait line."
    return best_arc, best_confidence, None


def calculate_markers(
    trajectory: Trajectory,
    junction_polygon: list[list[float]] | None,
    map_context: MapContext,
    origin: datetime | None = None,
    map_confidence: float | None = None,
    first_visible_t: float | None = None,
    full_view_t: float | None = None,
) -> list[TimestampMarker]:
    """Compute every configured timestamp marker for one event."""
    cfg = get_settings().section("geometry")
    distance_markers = [float(d) for d in cfg.get("distance_markers_m", [200.0, 100.0, 60.0])]
    post_distance = float(cfg.get("post_junction_marker_m", 20.0))

    markers: list[TimestampMarker] = []

    if not trajectory.valid:
        reason = trajectory.invalid_reason or "The ego trajectory is not usable."
        names = (
            [f"timestamp_{int(d)}m" for d in distance_markers]
            + [WAIT_LINE, JUNCTION_ENTRY, JUNCTION_EXIT, POST_JUNCTION]
        )
        return [
            TimestampMarker(name=name, available=False, unavailable_reason=reason, confidence=0.0)
            for name in names
        ]

    # --- object visibility markers (supplied by scene analysis) -----------
    for name, value in ((FIRST_VISIBLE, first_visible_t), (FULL_VIEW, full_view_t)):
        if value is None:
            markers.append(
                TimestampMarker(
                    name=name,
                    method="scene_analysis",
                    available=False,
                    unavailable_reason="Scene analysis did not determine this instant for the target object.",
                    confidence=0.0,
                )
            )
        else:
            markers.append(
                TimestampMarker(
                    name=name,
                    t=round(value, 4),
                    absolute=_absolute(origin, value),
                    method="scene_analysis",
                    pose_quality=trajectory.localization_quality,
                    confidence=round(min(1.0, (trajectory.localization_quality or 0.7)), 4),
                    available=True,
                )
            )

    # --- junction entry / exit -------------------------------------------
    entry_s: float | None = None
    exit_s: float | None = None
    geometry_reason: str | None = None
    if junction_polygon and len(junction_polygon) >= 3:
        entry_s, exit_s = entry_exit_arc_lengths(trajectory, junction_polygon)
        if entry_s is None:
            geometry_reason = "The ego trajectory never enters the selected junction polygon."
    else:
        geometry_reason = "No usable junction polygon was available for this event."

    markers.append(
        _marker(
            JUNCTION_ENTRY,
            trajectory=trajectory,
            arc_length=entry_s,
            origin=origin,
            map_confidence=map_confidence,
            distance_m=0.0,
            method="polygon_boundary_crossing",
            unavailable_reason=geometry_reason,
        )
    )
    markers.append(
        _marker(
            JUNCTION_EXIT,
            trajectory=trajectory,
            arc_length=exit_s,
            origin=origin,
            map_confidence=map_confidence,
            distance_m=0.0,
            method="polygon_boundary_crossing",
            unavailable_reason=(
                geometry_reason
                if geometry_reason
                else "The ego trajectory does not leave the junction polygon within the clip."
            ),
        )
    )

    # --- approach distance markers ----------------------------------------
    for distance in distance_markers:
        name = f"timestamp_{int(distance)}m"
        arc = (entry_s - distance) if entry_s is not None else None
        marker_reason: str | None = geometry_reason or (
            f"The clip starts less than {distance:.0f} m before the junction."
            if arc is not None and arc < 0
            else None
        )
        markers.append(
            _marker(
                name,
                trajectory=trajectory,
                arc_length=arc if (arc is not None and arc >= 0) else None,
                origin=origin,
                map_confidence=map_confidence,
                distance_m=distance,
                unavailable_reason=marker_reason,
            )
        )

    # --- wait line ---------------------------------------------------------
    wait_arc, wait_confidence, wait_reason = _wait_line_arc_length(trajectory, map_context)
    markers.append(
        _marker(
            WAIT_LINE,
            trajectory=trajectory,
            arc_length=wait_arc,
            origin=origin,
            map_confidence=wait_confidence if wait_confidence is not None else map_confidence,
            distance_m=(round(entry_s - wait_arc, 2) if (entry_s is not None and wait_arc is not None) else None),
            method="stop_line_intersection",
            unavailable_reason=wait_reason,
        )
    )

    # --- post-junction -----------------------------------------------------
    markers.append(
        _marker(
            POST_JUNCTION,
            trajectory=trajectory,
            arc_length=(exit_s + post_distance) if exit_s is not None else None,
            origin=origin,
            map_confidence=map_confidence,
            distance_m=post_distance,
            unavailable_reason=(
                geometry_reason
                if geometry_reason
                else f"The clip ends less than {post_distance:.0f} m after the junction exit."
            ),
        )
    )

    return markers_in_order(markers)


def markers_in_order(markers: list[TimestampMarker], names: list[str] | None = None) -> list[TimestampMarker]:
    """Return markers in canonical order; unknown names keep their relative order."""
    order = names or MARKER_ORDER
    rank = {name: i for i, name in enumerate(order)}
    return sorted(markers, key=lambda m: rank.get(m.name, len(rank)))
