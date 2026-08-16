"""Geometry, map and topology validation rules."""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point

from backend.configstore import RuleDefinition
from backend.models.contracts import ValidationOutcome
from backend.settings import get_settings
from backend.validation.registry import ValidationContext, fail, ok, rule, skip


def _edge_on_polygon(edge_id: str | None, ctx: ValidationContext) -> bool:
    junction = ctx.geometry.target_junction
    if not edge_id or junction is None:
        return False
    return edge_id.startswith(f"{junction.feature_id}#E")


@rule("GEOMETRY_POLYGON_MIN_POINTS")
def polygon_min_points(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    if ctx.geometry.target_junction is None:
        return skip(definition, "No target junction was selected, so there is no polygon to check.")
    minimum = int(definition.threshold or 3)
    assessment = ctx.geometry.polygon
    if assessment.unique_point_count >= minimum and not assessment.collinear:
        return ok(
            definition,
            f"Polygon has {assessment.unique_point_count} unique, non-collinear points.",
            observed={"unique_point_count": assessment.unique_point_count},
        )
    detail = (
        "all points are collinear"
        if assessment.collinear
        else f"only {assessment.unique_point_count} unique point(s) were supplied"
    )
    return fail(
        definition,
        f"Junction polygon is unusable: {detail} (minimum {minimum} non-collinear points required).",
        correction=(
            "Redraw the polygon on the Map & Lane page, or accept the recommended polygon if one "
            "was proposed."
        ),
        observed={
            "unique_point_count": assessment.unique_point_count,
            "collinear": assessment.collinear,
            "minimum": minimum,
        },
    )


@rule("GEOMETRY_POLYGON_VALID")
def polygon_valid(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    if ctx.geometry.target_junction is None:
        return skip(definition, "No target junction was selected, so there is no polygon to check.")
    assessment = ctx.geometry.polygon
    if assessment.unique_point_count < 3:
        return skip(definition, "The polygon has too few points to test for self-intersection.")
    if assessment.is_valid:
        return ok(definition, "Junction polygon is a simple, valid ring.")
    return fail(
        definition,
        "Junction polygon is invalid: " + "; ".join(assessment.issues or ["the ring is not simple"]) + ".",
        correction=(
            "Accept the recommended polygon (convex hull of the supplied points) or redraw it."
            if assessment.recommended_polygon
            else "Redraw the polygon on the Map & Lane page."
        ),
        observed={
            "self_intersecting": assessment.self_intersecting,
            "issues": assessment.issues,
            "recommended_point_count": len(assessment.recommended_polygon),
        },
    )


@rule("GEOMETRY_POLYGON_AREA_PLAUSIBLE")
def polygon_area(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    assessment = ctx.geometry.polygon
    if assessment.area_m2 is None:
        return skip(definition, "The polygon area could not be computed.")
    cfg = get_settings().section("geometry")
    minimum = float(cfg.get("min_junction_area_m2", 25.0))
    maximum = float(cfg.get("max_junction_area_m2", 40000.0))
    if minimum <= assessment.area_m2 <= maximum:
        return ok(
            definition,
            f"Polygon area {assessment.area_m2:.1f} m² is within the plausible range "
            f"{minimum:.0f}-{maximum:.0f} m².",
            observed={"area_m2": assessment.area_m2},
        )
    return fail(
        definition,
        f"Polygon area {assessment.area_m2:.1f} m² is outside the plausible range "
        f"{minimum:.0f}-{maximum:.0f} m².",
        correction="Confirm that the selected feature is the intended junction and not a whole road segment.",
        observed={"area_m2": assessment.area_m2, "min": minimum, "max": maximum},
    )


@rule("GEOMETRY_TRAJECTORY_INTERSECTS_JUNCTION")
def trajectory_intersects(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    junction = ctx.geometry.target_junction
    if junction is None:
        return skip(definition, "No target junction was selected.")
    if not ctx.trajectory.valid:
        return skip(definition, ctx.trajectory.invalid_reason or "The trajectory is not usable.")
    if junction.trajectory_intersects:
        return ok(definition, f"The ego trajectory passes through junction {junction.feature_id}.")
    distance = junction.distance_to_trajectory_m
    return fail(
        definition,
        f"The ego trajectory does not pass through junction {junction.feature_id}"
        + (f" (closest approach {distance:.1f} m)." if distance is not None else "."),
        correction=(
            "Review the ranked junction candidates on the Map & Lane page and select the junction "
            "the ego actually traverses."
        ),
        observed={"feature_id": junction.feature_id, "distance_m": distance},
    )


@rule("GEOMETRY_ENTRY_EDGE_ON_POLYGON")
def entry_edge_on_polygon(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    edge = ctx.geometry.entry_edge
    if edge is None:
        return skip(definition, "No entry edge was determined for this event.")
    if _edge_on_polygon(edge.edge_id, ctx):
        return ok(definition, f"Entry edge {edge.edge_id} belongs to the selected junction polygon.")
    return fail(
        definition,
        f"Entry edge {edge.edge_id} does not belong to the selected junction polygon.",
        correction="Re-derive the edges after confirming the junction selection.",
        observed={"entry_edge": edge.edge_id},
    )


@rule("GEOMETRY_EXIT_EDGE_ON_POLYGON")
def exit_edge_on_polygon(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    edge = ctx.geometry.exit_edge
    if edge is None:
        return skip(definition, "No exit edge was determined for this event.")
    if _edge_on_polygon(edge.edge_id, ctx):
        return ok(definition, f"Exit edge {edge.edge_id} belongs to the selected junction polygon.")
    return fail(
        definition,
        f"Exit edge {edge.edge_id} does not belong to the selected junction polygon.",
        correction="Re-derive the edges after confirming the junction selection.",
        observed={"exit_edge": edge.edge_id},
    )


def _crosses_edge(ctx: ValidationContext, edge_p1: list[float], edge_p2: list[float]) -> bool:
    if not ctx.trajectory.valid or len(ctx.trajectory.points) < 2:
        return False
    try:
        line = LineString([(p.x_m, p.y_m) for p in ctx.trajectory.points])
        segment = LineString([tuple(edge_p1), tuple(edge_p2)])
        return bool(line.intersects(segment))
    except (ValueError, TypeError):
        return False


@rule("GEOMETRY_EGO_CROSSES_ENTRY_EDGE")
def ego_crosses_entry(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    edge = ctx.geometry.entry_edge
    if edge is None:
        return skip(definition, "No entry edge was determined for this event.")
    if _crosses_edge(ctx, edge.p1, edge.p2):
        return ok(definition, f"The ego trajectory crosses entry edge {edge.edge_id}.")
    return fail(
        definition,
        f"The ego trajectory does not cross the nominated entry edge {edge.edge_id}.",
        correction="Select an alternative entry edge, or correct the junction polygon.",
        observed={"entry_edge": edge.edge_id, "p1": edge.p1, "p2": edge.p2},
    )


@rule("GEOMETRY_EGO_CROSSES_EXIT_EDGE")
def ego_crosses_exit(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    edge = ctx.geometry.exit_edge
    if edge is None:
        return skip(definition, "No exit edge was determined for this event.")
    if _crosses_edge(ctx, edge.p1, edge.p2):
        return ok(definition, f"The ego trajectory crosses exit edge {edge.edge_id}.")
    return fail(
        definition,
        f"The ego trajectory does not cross the nominated exit edge {edge.edge_id}.",
        correction="Select an alternative exit edge, or correct the junction polygon.",
        observed={"exit_edge": edge.edge_id, "p1": edge.p1, "p2": edge.p2},
    )


@rule("GEOMETRY_TOPOLOGY_PLAUSIBLE")
def topology_plausible(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    entry = ctx.geometry.entry_edge
    exit_edge = ctx.geometry.exit_edge
    if entry is None or exit_edge is None:
        return skip(definition, "Both an entry and an exit edge are needed to test topology.")
    if entry.edge_id == exit_edge.edge_id:
        return fail(
            definition,
            f"Entry and exit resolved to the same edge ({entry.edge_id}), which is not a plausible "
            "traversal of a junction.",
            correction="Choose distinct entry and exit edges, or re-check the junction polygon shape.",
            observed={"entry_edge": entry.edge_id, "exit_edge": exit_edge.edge_id},
        )
    return ok(
        definition,
        f"Entry ({entry.edge_id}) and exit ({exit_edge.edge_id}) are distinct edges.",
    )


@rule("GEOMETRY_MAP_ALIGNMENT")
def map_alignment(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    offset = ctx.geometry.map_alignment_offset_m
    if offset is None:
        return skip(
            definition,
            "No mapped lane centreline was available, so trajectory-to-map alignment could not be measured.",
        )
    limit = float(get_settings().section("geometry").get("max_map_alignment_offset_m", 5.0))
    if offset <= limit:
        return ok(
            definition,
            f"Maximum lateral offset between the ego trajectory and the mapped centreline is "
            f"{offset:.2f} m (limit {limit:.1f} m).",
            observed={"max_offset_m": offset, "limit_m": limit},
        )
    return fail(
        definition,
        f"Maximum lateral offset between the ego trajectory and the mapped centreline is "
        f"{offset:.2f} m, which exceeds the {limit:.1f} m limit.",
        correction=(
            "Check localisation quality and the HD map version. A large offset makes every "
            "distance-based timestamp unreliable."
        ),
        observed={"max_offset_m": offset, "limit_m": limit},
    )


@rule("MAP_CONTEXT_AVAILABLE")
def map_context_available(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    context = ctx.bundle.map_context
    if context.available and context.features:
        return ok(
            definition,
            f"Map context available with {len(context.features)} feature(s).",
            observed={"feature_count": len(context.features)},
        )
    return fail(
        definition,
        "No map context is available for this event: "
        + (context.unavailable_reason or "the source supplied none")
        + ".",
        correction=(
            "Configure an HD map service on the Connections page, or re-export the event with its "
            "map context. Junction selection, edges and distance markers cannot be derived without it."
        ),
        observed={"unavailable_reason": context.unavailable_reason},
    )


@rule("MAP_VERSION_RECORDED")
def map_version_recorded(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    version = ctx.bundle.map_context.map_version or ctx.metadata.map_version
    if version:
        return ok(definition, f"Map version {version} recorded for reproducibility.")
    return fail(
        definition,
        "No HD map version was recorded for this event.",
        correction="Ensure the source exposes the map version so results remain reproducible.",
    )


def max_lateral_offset(ctx: ValidationContext) -> float | None:
    """Largest perpendicular distance from the trajectory to any mapped centreline.

    Exposed for the geometry stage so the value is computed once and reused by
    both the rule and the confidence engine.
    """
    if not ctx.trajectory.valid or not ctx.bundle.map_context.available:
        return None
    centerlines = ctx.bundle.map_context.by_type("lane_centerline")
    if not centerlines:
        return None

    lines: list[LineString] = []
    for feature in centerlines:
        coords = feature.geometry.coordinates
        if feature.geometry.type == "LineString" and len(coords) >= 2:
            try:
                lines.append(LineString([(float(c[0]), float(c[1])) for c in coords]))
            except (ValueError, TypeError):
                continue
    if not lines:
        return None

    worst = 0.0
    for trajectory_point in ctx.trajectory.points:
        here = Point(trajectory_point.x_m, trajectory_point.y_m)
        nearest = min(line.distance(here) for line in lines)
        if not math.isnan(nearest):
            worst = max(worst, float(nearest))
    return round(worst, 3)
