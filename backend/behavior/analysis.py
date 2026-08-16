"""Ego behaviour analysis.

The hard rule this module exists to enforce:

    An OBSERVATION states what was measured.
    An INTERPRETATION states why it happened.

This engine only ever produces observations::

    "Ego speed remained below 0.30 m/s for 2.10 s, ending 4.2 m before the
     mapped wait line."

It never writes::

    "Vehicle stopped because of crossing traffic."

The ``interpretation`` field on every observation is left ``None`` and is only
ever filled in by a human reviewer. Stop classification uses configured,
non-safety thresholds and is reported as a *classification of the measurement*,
not as a pass/fail judgement of the vehicle.
"""

from __future__ import annotations

from typing import Any

from backend.geometry.trajectory import arc_length_at_time, heading_change_deg
from backend.models.contracts import (
    AbnormalityCategory,
    BehaviorAnalysis,
    BehaviorObservation,
    RecordStatus,
    SceneFinding,
    Severity,
    TimestampMarker,
    Trajectory,
)
from backend.settings import get_settings

STOP_NOT_APPLICABLE = "not_applicable"
STOP_NONE = "no_stop_detected"
STOP_ROLLING = "rolling_stop_candidate"
STOP_FULL = "full_stop_detected"
STOP_STATIONARY = "stationary_throughout"
STOP_UNKNOWN = "unknown"


def _cfg() -> dict[str, Any]:
    return get_settings().section("behavior")


def _longest_below(points: list[tuple[float, float]], threshold: float) -> tuple[float, float, float]:
    """Longest contiguous span with speed <= ``threshold``.

    Returns ``(duration_s, t_start, t_end)``; zeros when never below.
    """
    best = (0.0, 0.0, 0.0)
    start: float | None = None
    previous_t: float | None = None
    for t, speed in points:
        if speed <= threshold:
            if start is None:
                start = t
            previous_t = t
        else:
            if start is not None and previous_t is not None:
                duration = previous_t - start
                if duration > best[0]:
                    best = (duration, start, previous_t)
            start, previous_t = None, None
    if start is not None and previous_t is not None:
        duration = previous_t - start
        if duration > best[0]:
            best = (duration, start, previous_t)
    return best


def _classify_maneuver(heading_delta: float | None, cfg: dict[str, Any]) -> str:
    if heading_delta is None:
        return "unknown"
    turn_threshold = float(cfg.get("turn_heading_change_deg", 35.0))
    u_turn_threshold = float(cfg.get("u_turn_heading_change_deg", 150.0))
    magnitude = abs(heading_delta)
    if magnitude >= u_turn_threshold:
        return "u_turn"
    if magnitude >= turn_threshold:
        return "left_turn" if heading_delta > 0 else "right_turn"
    return "straight"


def analyse_behavior(
    trajectory: Trajectory,
    markers: list[TimestampMarker] | None = None,
) -> BehaviorAnalysis:
    """Measure the ego's approach, stopping and turning behaviour."""
    cfg = _cfg()
    stop_speed = float(cfg.get("stop_speed_threshold_mps", 0.3))
    full_stop_duration = float(cfg.get("full_stop_min_duration_s", 1.0))
    decel_threshold = float(cfg.get("deceleration_threshold_mps2", -0.8))
    accel_threshold = float(cfg.get("acceleration_threshold_mps2", 0.8))

    if not trajectory.valid or len(trajectory.points) < 3:
        return BehaviorAnalysis(
            available=False,
            unavailable_reason=(
                trajectory.invalid_reason
                or "Too few trajectory points to measure behaviour."
            ),
            stop_classification=STOP_UNKNOWN,
        )

    speeds = [(p.t, p.speed_mps) for p in trajectory.points if p.speed_mps is not None]
    if len(speeds) < 3:
        return BehaviorAnalysis(
            available=False,
            unavailable_reason=(
                "The trajectory carries no speed channel, so stop and deceleration behaviour "
                "cannot be measured. Enable the vehicle_state or CAN stream for this run."
            ),
            stop_classification=STOP_UNKNOWN,
        )

    observations: list[BehaviorObservation] = []
    findings: list[SceneFinding] = []

    minimum_speed = min(s for _, s in speeds)
    maximum_speed = max(s for _, s in speeds)
    t_at_min = next(t for t, s in speeds if s == minimum_speed)

    stop_duration, stop_start, stop_end = _longest_below(speeds, stop_speed)

    # --- stop classification (a description of the measurement) ------------
    if maximum_speed <= stop_speed:
        stop_classification = STOP_STATIONARY
    elif stop_duration >= full_stop_duration:
        stop_classification = STOP_FULL
    elif stop_duration > 0.0:
        stop_classification = STOP_ROLLING
    else:
        stop_classification = STOP_NONE

    observations.append(
        BehaviorObservation(
            name="minimum_speed",
            observation=f"Minimum ego speed was {minimum_speed:.2f} m/s at t={t_at_min:.2f}s.",
            t_start=round(t_at_min, 3),
            value=round(minimum_speed, 3),
            unit="m/s",
        )
    )
    if stop_duration > 0.0:
        observations.append(
            BehaviorObservation(
                name="below_stop_threshold",
                observation=(
                    f"Ego speed remained at or below the configured stop threshold of "
                    f"{stop_speed:.2f} m/s for {stop_duration:.2f} s "
                    f"({stop_start:.2f}s - {stop_end:.2f}s)."
                ),
                t_start=round(stop_start, 3),
                t_end=round(stop_end, 3),
                value=round(stop_duration, 3),
                unit="s",
            )
        )
    else:
        observations.append(
            BehaviorObservation(
                name="below_stop_threshold",
                observation=(
                    f"Ego speed never fell to or below the configured stop threshold of "
                    f"{stop_speed:.2f} m/s."
                ),
                value=0.0,
                unit="s",
            )
        )

    # --- deceleration / re-acceleration ------------------------------------
    accelerations = [(p.t, p.speed_mps) for p in trajectory.points if p.speed_mps is not None]
    derived: list[tuple[float, float]] = []
    for (t0, v0), (t1, v1) in zip(accelerations, accelerations[1:], strict=False):
        dt = t1 - t0
        if dt > 0:
            derived.append((t1, (v1 - v0) / dt))

    min_accel = min((a for _, a in derived), default=0.0)
    max_accel = max((a for _, a in derived), default=0.0)
    deceleration_detected = min_accel <= decel_threshold
    reacceleration_detected = any(
        a >= accel_threshold for t, a in derived if stop_end and t > stop_end
    ) or (max_accel >= accel_threshold and stop_duration > 0)

    if deceleration_detected:
        t_min_accel = next(t for t, a in derived if a == min_accel)
        observations.append(
            BehaviorObservation(
                name="deceleration",
                observation=(
                    f"Peak deceleration of {min_accel:.2f} m/s² at t={t_min_accel:.2f}s, "
                    f"below the configured {decel_threshold:.2f} m/s² threshold."
                ),
                t_start=round(t_min_accel, 3),
                value=round(min_accel, 3),
                unit="m/s^2",
            )
        )
    if reacceleration_detected:
        observations.append(
            BehaviorObservation(
                name="reacceleration",
                observation=f"Peak acceleration of {max_accel:.2f} m/s² observed after the minimum-speed instant.",
                value=round(max_accel, 3),
                unit="m/s^2",
            )
        )

    approach_detected = deceleration_detected and minimum_speed < maximum_speed * 0.8
    if approach_detected:
        observations.append(
            BehaviorObservation(
                name="approach",
                observation=(
                    f"Ego speed fell from {maximum_speed:.2f} m/s to {minimum_speed:.2f} m/s "
                    "before the minimum-speed instant."
                ),
                value=round(maximum_speed - minimum_speed, 3),
                unit="m/s",
            )
        )

    # --- manoeuvre ----------------------------------------------------------
    delta_heading = heading_change_deg(trajectory)
    maneuver = _classify_maneuver(delta_heading, cfg)
    if delta_heading is not None:
        observations.append(
            BehaviorObservation(
                name="heading_change",
                observation=f"Net heading change over the clip was {delta_heading:.1f}°.",
                value=delta_heading,
                unit="deg",
            )
        )

    # --- distance to the mapped wait line ------------------------------------
    # Only meaningful when the ego actually slowed: without a stop, the
    # "minimum speed position" is just wherever the clip happened to start, and
    # reporting a distance from there would be misleading.
    wait_line_distance: float | None = None
    marker_index = {m.name: m for m in (markers or [])}
    wait_marker = marker_index.get("wait_line_crossing")
    slowed = stop_classification in (STOP_FULL, STOP_ROLLING, STOP_STATIONARY)
    if slowed and wait_marker is not None and wait_marker.available and wait_marker.t is not None:
        wait_arc = arc_length_at_time(trajectory, wait_marker.t)
        stop_arc = arc_length_at_time(trajectory, t_at_min)
        if wait_arc is not None and stop_arc is not None:
            wait_line_distance = round(wait_arc - stop_arc, 3)
            side = "before" if wait_line_distance >= 0 else "past"
            observations.append(
                BehaviorObservation(
                    name="wait_line_distance",
                    observation=(
                        f"The minimum-speed position was {abs(wait_line_distance):.2f} m {side} "
                        "the mapped wait line."
                    ),
                    value=wait_line_distance,
                    unit="m",
                )
            )

    # --- rolling-stop CANDIDATE (never a verdict) ----------------------------
    if stop_classification == STOP_ROLLING:
        findings.append(
            SceneFinding(
                code="BEHAVIOR_ROLLING_STOP_CANDIDATE",
                category=AbnormalityCategory.BEHAVIOR,
                severity=Severity.WARNING,
                message=(
                    f"Ego slowed to {minimum_speed:.2f} m/s and stayed at or below the "
                    f"{stop_speed:.2f} m/s stop threshold for {stop_duration:.2f} s, which is less "
                    f"than the {full_stop_duration:.2f} s configured for a full stop. "
                    "This is an observation; whether it constitutes a defect requires human review "
                    "against the applicable project rule."
                ),
                t=round(stop_start, 3),
                evidence={
                    "minimum_speed_mps": round(minimum_speed, 3),
                    "stop_duration_s": round(stop_duration, 3),
                    "stop_speed_threshold_mps": stop_speed,
                    "full_stop_min_duration_s": full_stop_duration,
                    "wait_line_distance_m": wait_line_distance,
                },
                status=RecordStatus.CANDIDATE,
                requires_review=True,
            )
        )

    # Confidence in the behaviour measurement itself: driven by how much of the
    # trajectory carried a speed channel and by localisation quality.
    coverage = len(speeds) / max(1, len(trajectory.points))
    confidence = round(min(1.0, coverage * (trajectory.localization_quality or 0.8)), 4)

    return BehaviorAnalysis(
        available=True,
        observations=observations,
        maneuver=maneuver,
        heading_change_deg=delta_heading,
        approach_detected=approach_detected,
        deceleration_detected=deceleration_detected,
        reacceleration_detected=reacceleration_detected,
        stop_classification=stop_classification,
        minimum_speed_mps=round(minimum_speed, 3),
        stop_duration_s=round(stop_duration, 3) if stop_duration > 0 else 0.0,
        wait_line_distance_m=wait_line_distance,
        findings=findings,
        confidence=confidence,
    )
