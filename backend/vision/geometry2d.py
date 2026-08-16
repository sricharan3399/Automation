"""Small 2D helpers shared by the vision engines."""

from __future__ import annotations

from typing import Any

Box = dict[str, float]


def valid_box(box: Any) -> bool:
    return (
        isinstance(box, dict)
        and all(k in box for k in ("x", "y", "w", "h"))
        and float(box["w"]) > 0
        and float(box["h"]) > 0
    )


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two normalised boxes."""
    if not valid_box(a) or not valid_box(b):
        return 0.0
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["w"]), ay1 + float(a["h"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["w"]), by1 + float(b["h"])

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    return round(intersection / union, 4) if union > 0 else 0.0


def fully_in_view(box: Box, margin: float = 0.02) -> bool:
    """True when the whole box sits inside the image with a margin."""
    if not valid_box(box):
        return False
    x, y, w, h = float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])
    return x >= margin and y >= margin and (x + w) <= (1.0 - margin) and (y + h) <= (1.0 - margin)


def box_area(box: Box) -> float:
    return float(box["w"]) * float(box["h"]) if valid_box(box) else 0.0


def merge_intervals(times: list[float], max_gap: float) -> list[tuple[float, float]]:
    """Group sorted timestamps into contiguous intervals."""
    if not times:
        return []
    ordered = sorted(times)
    intervals: list[tuple[float, float]] = []
    start = previous = ordered[0]
    for t in ordered[1:]:
        if t - previous > max_gap:
            intervals.append((start, previous))
            start = t
        previous = t
    intervals.append((start, previous))
    return intervals
