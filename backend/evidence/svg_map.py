"""Self-contained SVG rendering of the map, trajectory and derived markers.

Deliberately dependency-free and offline: no tile server is contacted, so no
positional data ever leaves the workstation. The output is a single SVG file
that opens in any browser and embeds nothing external.

Coordinates are the event's local metric frame (x=east, y=north, metres), which
is also what keeps precise global positions out of exported evidence.
"""

from __future__ import annotations

from typing import Any

from backend.models.contracts import GeometryResult, MapContext, Trajectory

WIDTH = 900
HEIGHT = 700
PADDING = 48

STYLE = """
  .bg { fill: var(--bg, #0f1419); }
  .lane { stroke: #4a5568; stroke-width: 1.6; fill: none; stroke-dasharray: 6 5; }
  .stopline { stroke: #f6ad55; stroke-width: 3; }
  .junction { fill: rgba(66,153,225,0.14); stroke: #4299e1; stroke-width: 2; }
  .junction-alt { fill: none; stroke: #718096; stroke-width: 1.4; stroke-dasharray: 4 4; }
  .traj { stroke: #48bb78; stroke-width: 2.6; fill: none; }
  .entry { stroke: #38b2ac; stroke-width: 5; stroke-linecap: round; }
  .exit { stroke: #ed64a6; stroke-width: 5; stroke-linecap: round; }
  .marker { fill: #f6e05e; stroke: #1a202c; stroke-width: 1; }
  .signal { fill: #fc8181; }
  .label { fill: #e2e8f0; font: 11px 'Segoe UI', system-ui, sans-serif; }
  .title { fill: #f7fafc; font: 600 15px 'Segoe UI', system-ui, sans-serif; }
  .legend { fill: #a0aec0; font: 11px 'Segoe UI', system-ui, sans-serif; }
"""


class _Projection:
    """Maps local metric coordinates onto SVG pixels, preserving aspect ratio."""

    def __init__(self, points: list[tuple[float, float]]) -> None:
        xs = [p[0] for p in points] or [0.0]
        ys = [p[1] for p in points] or [0.0]
        self.min_x, self.max_x = min(xs), max(xs)
        self.min_y, self.max_y = min(ys), max(ys)
        span_x = max(self.max_x - self.min_x, 1.0)
        span_y = max(self.max_y - self.min_y, 1.0)
        self.scale = min((WIDTH - 2 * PADDING) / span_x, (HEIGHT - 2 * PADDING) / span_y)
        self.offset_x = (WIDTH - span_x * self.scale) / 2 - self.min_x * self.scale
        self.offset_y = (HEIGHT - span_y * self.scale) / 2 - self.min_y * self.scale

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        # SVG y grows downwards; north should point up.
        return (
            round(x * self.scale + self.offset_x, 2),
            round(HEIGHT - (y * self.scale + self.offset_y), 2),
        )


def _escape(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_map_svg(
    trajectory: Trajectory,
    geometry: GeometryResult,
    map_context: MapContext,
    title: str = "Ego trajectory and junction geometry",
) -> str:
    """Render the whole geometric picture of one event as a standalone SVG."""
    points: list[tuple[float, float]] = [(p.x_m, p.y_m) for p in trajectory.points]
    for feature in map_context.features if map_context.available else []:
        coords = feature.geometry.coordinates
        if feature.geometry.type == "Point" and len(coords) >= 2:
            points.append((float(coords[0]), float(coords[1])))
        elif feature.geometry.type == "LineString":
            points.extend((float(c[0]), float(c[1])) for c in coords if len(c) >= 2)
        elif feature.geometry.type == "Polygon" and coords:
            points.extend((float(c[0]), float(c[1])) for c in coords[0] if len(c) >= 2)

    if not points:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
            f'viewBox="0 0 {WIDTH} {HEIGHT}"><style>{STYLE}</style>'
            f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}"/>'
            f'<text class="title" x="24" y="36">No geometry available for this event</text></svg>'
        )

    project = _Projection(points)
    body: list[str] = [
        f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}"/>',
        f'<text class="title" x="24" y="30">{_escape(title)}</text>',
    ]

    # --- map features -----------------------------------------------------
    selected_id = geometry.target_junction.feature_id if geometry.target_junction else None
    if map_context.available:
        for feature in map_context.features:
            coords = feature.geometry.coordinates
            if feature.geometry.type == "LineString" and len(coords) >= 2:
                path = " ".join(
                    f"{'M' if i == 0 else 'L'}{project(float(c[0]), float(c[1]))[0]},{project(float(c[0]), float(c[1]))[1]}"
                    for i, c in enumerate(coords)
                    if len(c) >= 2
                )
                css = "stopline" if feature.feature_type == "stop_line" else "lane"
                body.append(f'<path class="{css}" d="{path}"/>')
            elif feature.geometry.type == "Polygon" and coords:
                ring = coords[0]
                pts = " ".join(f"{project(float(c[0]), float(c[1]))[0]},{project(float(c[0]), float(c[1]))[1]}" for c in ring if len(c) >= 2)
                css = "junction" if feature.feature_id == selected_id else "junction-alt"
                body.append(f'<polygon class="{css}" points="{pts}"/>')
                if ring:
                    cx = sum(float(c[0]) for c in ring) / len(ring)
                    cy = sum(float(c[1]) for c in ring) / len(ring)
                    lx, ly = project(cx, cy)
                    body.append(f'<text class="label" x="{lx + 6}" y="{ly - 6}">{_escape(feature.feature_id)}</text>')
            elif feature.geometry.type == "Point" and len(coords) >= 2:
                px, py = project(float(coords[0]), float(coords[1]))
                body.append(f'<circle class="signal" cx="{px}" cy="{py}" r="5"/>')
                body.append(f'<text class="label" x="{px + 8}" y="{py + 4}">{_escape(feature.feature_id)}</text>')

    # --- trajectory --------------------------------------------------------
    if trajectory.points:
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{project(p.x_m, p.y_m)[0]},{project(p.x_m, p.y_m)[1]}"
            for i, p in enumerate(trajectory.points)
        )
        body.append(f'<path class="traj" d="{path}"/>')
        sx, sy = project(trajectory.points[0].x_m, trajectory.points[0].y_m)
        body.append(f'<circle cx="{sx}" cy="{sy}" r="4" fill="#48bb78"/>')
        body.append(f'<text class="label" x="{sx + 8}" y="{sy + 4}">start</text>')

    # --- entry / exit edges -------------------------------------------------
    for edge, css, label in (
        (geometry.entry_edge, "entry", "entry"),
        (geometry.exit_edge, "exit", "exit"),
    ):
        if edge is None:
            continue
        x1, y1 = project(edge.p1[0], edge.p1[1])
        x2, y2 = project(edge.p2[0], edge.p2[1])
        body.append(f'<line class="{css}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')
        body.append(
            f'<text class="label" x="{(x1 + x2) / 2 + 6}" y="{(y1 + y2) / 2 - 6}">'
            f"{label} ({edge.confidence:.2f})</text>"
        )

    # --- timestamp markers ---------------------------------------------------
    from backend.geometry.trajectory import point_at_arc_length  # local import avoids a cycle

    arcs = {p.t: p.arc_length_m for p in trajectory.points}
    for marker in geometry.markers:
        if not marker.available or marker.t is None or not arcs:
            continue
        nearest_t = min(arcs, key=lambda t: abs(t - (marker.t or 0.0)))
        point = point_at_arc_length(trajectory, arcs[nearest_t])
        if point is None:
            continue
        mx, my = project(point.x_m, point.y_m)
        body.append(f'<circle class="marker" cx="{mx}" cy="{my}" r="4.5"/>')
        body.append(f'<text class="label" x="{mx + 8}" y="{my - 6}">{_escape(marker.name)}</text>')

    # --- legend ---------------------------------------------------------------
    legend = [
        ("#48bb78", "ego trajectory"),
        ("#4299e1", "selected junction"),
        ("#718096", "other junction"),
        ("#38b2ac", "entry edge"),
        ("#ed64a6", "exit edge"),
        ("#f6e05e", "derived marker"),
        ("#f6ad55", "stop / wait line"),
    ]
    for index, (colour, label) in enumerate(legend):
        y = HEIGHT - 20 - index * 16
        body.append(f'<rect x="24" y="{y - 9}" width="10" height="10" fill="{colour}"/>')
        body.append(f'<text class="legend" x="42" y="{y}">{_escape(label)}</text>')

    scale_note = f"scale: 1 px = {1 / project.scale:.2f} m - local metric frame, no global coordinates"
    body.append(f'<text class="legend" x="{WIDTH - 24}" y="{HEIGHT - 20}" text-anchor="end">{_escape(scale_note)}</text>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{_escape(title)}">'
        f"<style>{STYLE}</style>" + "".join(body) + "</svg>"
    )
