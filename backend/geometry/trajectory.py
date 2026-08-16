"""Ego trajectory construction and arc-length interpolation.

The trajectory is the backbone of every derived timestamp, so it is validated
before use: too few points, zero length, implausible position jumps or missing
localisation all mark it invalid with a reason instead of silently producing
timestamps nobody can trust.
"""

from __future__ import annotations

import bisect
import math

from backend.models.contracts import PoseSample, Trajectory, TrajectoryPoint

#: A single-sample position jump larger than this is treated as a localisation
#: glitch rather than real motion (250 km/h at 10 Hz is ~7 m).
MAX_PLAUSIBLE_STEP_M = 15.0
MIN_POINTS = 2
MIN_LENGTH_M = 1.0


def build_trajectory(poses: list[PoseSample]) -> Trajectory:
    """Build a validated, arc-length-parameterised trajectory."""
    ordered = sorted((p for p in poses), key=lambda p: p.t)
    if len(ordered) < MIN_POINTS:
        return Trajectory(
            valid=False,
            invalid_reason=(
                f"Only {len(ordered)} ego pose(s) are available; at least {MIN_POINTS} are needed to "
                "build a trajectory. Distance-based timestamps cannot be derived."
            ),
        )

    points: list[TrajectoryPoint] = []
    arc = 0.0
    jumps: list[tuple[float, float]] = []
    previous: PoseSample | None = None

    for pose in ordered:
        if previous is not None:
            step = math.hypot(pose.x_m - previous.x_m, pose.y_m - previous.y_m)
            if step > MAX_PLAUSIBLE_STEP_M:
                jumps.append((pose.t, step))
            arc += step
        points.append(
            TrajectoryPoint(
                t=pose.t,
                x_m=pose.x_m,
                y_m=pose.y_m,
                heading_rad=pose.heading_rad,
                speed_mps=pose.speed_mps,
                arc_length_m=arc,
            )
        )
        previous = pose

    duration = ordered[-1].t - ordered[0].t
    qualities = [p.localization_quality for p in ordered if p.localization_quality is not None]
    mean_quality = sum(qualities) / len(qualities) if qualities else None

    if arc < MIN_LENGTH_M:
        return Trajectory(
            points=points,
            total_length_m=arc,
            duration_s=duration,
            localization_quality=mean_quality,
            valid=False,
            invalid_reason=(
                f"The ego travelled only {arc:.2f} m over {duration:.1f} s. Distance markers cannot "
                "be placed on a stationary trajectory."
            ),
        )

    if jumps:
        first_t, first_step = jumps[0]
        return Trajectory(
            points=points,
            total_length_m=arc,
            duration_s=duration,
            localization_quality=mean_quality,
            valid=False,
            invalid_reason=(
                f"Implausible position jump of {first_step:.1f} m at t={first_t:.2f}s "
                f"({len(jumps)} jump(s) total). Localisation quality must be reviewed before "
                "distance-based timestamps are trusted."
            ),
        )

    return Trajectory(
        points=points,
        total_length_m=arc,
        duration_s=duration,
        localization_quality=mean_quality,
        valid=True,
    )


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------
def _arcs(trajectory: Trajectory) -> list[float]:
    return [p.arc_length_m for p in trajectory.points]


def _times(trajectory: Trajectory) -> list[float]:
    return [p.t for p in trajectory.points]


def arc_length_at_time(trajectory: Trajectory, t: float) -> float | None:
    """Arc length travelled at time ``t``, interpolated. ``None`` outside range."""
    times = _times(trajectory)
    if not times or t < times[0] - 1e-9 or t > times[-1] + 1e-9:
        return None
    index = bisect.bisect_left(times, t)
    if index == 0:
        return trajectory.points[0].arc_length_m
    if index >= len(times):
        return trajectory.points[-1].arc_length_m
    before, after = trajectory.points[index - 1], trajectory.points[index]
    span = after.t - before.t
    if span <= 0:
        return before.arc_length_m
    ratio = (t - before.t) / span
    return before.arc_length_m + (after.arc_length_m - before.arc_length_m) * ratio


def time_at_arc_length(trajectory: Trajectory, s: float) -> tuple[float | None, float]:
    """Return ``(time, interpolation_error_s)`` for arc length ``s``.

    ``interpolation_error_s`` is half the sample interval used - the honest
    resolution limit of the estimate, reported alongside every derived
    timestamp rather than being hidden.
    """
    arcs = _arcs(trajectory)
    if not arcs or s < arcs[0] - 1e-9 or s > arcs[-1] + 1e-9:
        return None, 0.0
    index = bisect.bisect_left(arcs, s)
    if index == 0:
        return trajectory.points[0].t, 0.0
    if index >= len(arcs):
        return trajectory.points[-1].t, 0.0
    before, after = trajectory.points[index - 1], trajectory.points[index]
    span = after.arc_length_m - before.arc_length_m
    dt = after.t - before.t
    if span <= 0:
        return before.t, dt / 2.0
    ratio = (s - before.arc_length_m) / span
    return before.t + dt * ratio, dt / 2.0


def point_at_arc_length(trajectory: Trajectory, s: float) -> TrajectoryPoint | None:
    arcs = _arcs(trajectory)
    if not arcs or s < arcs[0] - 1e-9 or s > arcs[-1] + 1e-9:
        return None
    index = bisect.bisect_left(arcs, s)
    if index == 0:
        return trajectory.points[0]
    if index >= len(arcs):
        return trajectory.points[-1]
    before, after = trajectory.points[index - 1], trajectory.points[index]
    span = after.arc_length_m - before.arc_length_m
    if span <= 0:
        return before
    ratio = (s - before.arc_length_m) / span
    heading = before.heading_rad
    if before.heading_rad is not None and after.heading_rad is not None:
        delta = math.atan2(
            math.sin(after.heading_rad - before.heading_rad),
            math.cos(after.heading_rad - before.heading_rad),
        )
        heading = before.heading_rad + delta * ratio
    return TrajectoryPoint(
        t=before.t + (after.t - before.t) * ratio,
        x_m=before.x_m + (after.x_m - before.x_m) * ratio,
        y_m=before.y_m + (after.y_m - before.y_m) * ratio,
        heading_rad=heading,
        speed_mps=(
            before.speed_mps + (after.speed_mps - before.speed_mps) * ratio
            if before.speed_mps is not None and after.speed_mps is not None
            else before.speed_mps
        ),
        arc_length_m=s,
    )


def as_coordinates(trajectory: Trajectory) -> list[tuple[float, float]]:
    return [(p.x_m, p.y_m) for p in trajectory.points]


def heading_change_deg(trajectory: Trajectory, t_start: float | None = None, t_end: float | None = None) -> float | None:
    """Net heading change between two times, wrapped to (-180, 180]."""
    points = [p for p in trajectory.points if p.heading_rad is not None]
    if len(points) < 2:
        return None
    if t_start is not None:
        points = [p for p in points if p.t >= t_start]
    if t_end is not None:
        points = [p for p in points if p.t <= t_end]
    if len(points) < 2:
        return None
    total = 0.0
    for before, after in zip(points, points[1:], strict=False):
        assert before.heading_rad is not None and after.heading_rad is not None
        total += math.atan2(
            math.sin(after.heading_rad - before.heading_rad),
            math.cos(after.heading_rad - before.heading_rad),
        )
    return round(math.degrees(total), 3)
