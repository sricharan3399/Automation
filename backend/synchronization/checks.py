"""Stream health and synchronisation analysis.

Detects, per stream:

* missing stream / missing samples
* dropped frames (against the declared nominal rate)
* duplicate samples (identical timestamps within tolerance)
* frozen stream (payload signature unchanged over a sustained window)
* timestamp gaps
* non-monotonic timestamps
* fixed offset to the master clock and linear drift

Every threshold comes from ``config/base.yaml``. Nothing here decides that a
finding is a vehicle failure; it decides that the *data* is or is not usable.
"""

from __future__ import annotations

from typing import Any

from backend.models.contracts import (
    SensorConfiguration,
    StreamHealth,
    StreamManifestEntry,
    StreamRequirement,
    SynchronizationReport,
)
from backend.settings import get_settings
from backend.synchronization.timeline import MasterTimeline

CAMERA_TYPES = {"camera"}
TELEMETRY_TYPES = {"vehicle_state", "can", "imu", "gps", "localization"}


def _sync_config() -> dict[str, Any]:
    return get_settings().section("synchronization")


def _stream_key(entry: StreamManifestEntry) -> str:
    return f"{entry.stream_type}:{entry.camera_position}" if entry.camera_position else entry.stream_type


def _longest_identical_run(signatures: list[str | None]) -> int:
    """Longest run of identical, non-null signatures."""
    best = current = 0
    previous: str | None = None
    for signature in signatures:
        if signature is not None and signature == previous:
            current += 1
            best = max(best, current)
        else:
            current = 1 if signature is not None else 0
            previous = signature
    return best


def _estimate_drift_ms_per_s(times: list[float], nominal_rate_hz: float | None) -> float | None:
    """Least-squares slope of sample time vs sample index, vs the nominal period.

    A clock running fast or slow shows up as a slope that differs from
    ``1 / nominal_rate``; the deviation is reported in ms per second.
    """
    if nominal_rate_hz in (None, 0) or len(times) < 10:
        return None
    n = len(times)
    mean_i = (n - 1) / 2.0
    mean_t = sum(times) / n
    numerator = sum((i - mean_i) * (t - mean_t) for i, t in enumerate(times))
    denominator = sum((i - mean_i) ** 2 for i in range(n))
    if denominator == 0:
        return None
    slope = numerator / denominator
    expected = 1.0 / float(nominal_rate_hz)
    if expected <= 0:
        return None
    return round((slope - expected) / expected * 1000.0, 4)


def analyse_streams(
    manifest: list[StreamManifestEntry],
    config: SensorConfiguration,
    timeline: MasterTimeline,
) -> list[StreamHealth]:
    """Compute health for every configured and supplied stream."""
    cfg = _sync_config()
    dup_tolerance_s = float(cfg.get("duplicate_timestamp_tolerance_ms", 0.5)) / 1000.0
    max_gap_ms = float(cfg.get("max_timestamp_gap_ms", 200.0))
    frozen_seconds = float(cfg.get("frozen_stream_seconds", 2.0))
    min_availability = float(cfg.get("min_stream_availability_pct", 95.0))

    supplied_keys = {_stream_key(e) for e in manifest}
    results: list[StreamHealth] = []

    # 1. Streams the tester requires that the source never mentioned at all.
    for required_key in config.required_keys():
        if required_key in supplied_keys:
            continue
        stream_type, _, camera = required_key.partition(":")
        results.append(
            StreamHealth(
                stream_type=stream_type,
                camera_position=camera or None,
                requirement=StreamRequirement.REQUIRED,
                present=False,
                status="blocking",
                issues=["MISSING_SENSOR_STREAM"],
                quality_score=0.0,
                availability_pct=0.0,
            )
        )

    # 2. Streams the source supplied.
    for entry in manifest:
        requirement = config.requirement_for(entry.stream_type, entry.camera_position)
        if requirement == StreamRequirement.IGNORE:
            continue

        health = StreamHealth(
            stream_type=entry.stream_type,
            camera_position=entry.camera_position,
            requirement=requirement,
            present=bool(entry.present and entry.samples),
            sample_count=len(entry.samples),
        )

        if not health.present:
            health.status = "blocking" if requirement == StreamRequirement.REQUIRED else "missing"
            health.issues.append("MISSING_SENSOR_STREAM")
            health.availability_pct = 0.0
            health.quality_score = 0.0
            results.append(health)
            continue

        times = sorted(s.t for s in entry.samples)
        span = times[-1] - times[0]

        # Expected sample count from the declared nominal rate.
        if entry.nominal_rate_hz:
            expected = int(round(span * float(entry.nominal_rate_hz))) + 1
            health.expected_sample_count = max(expected, 1)
            health.availability_pct = round(
                min(100.0, len(times) / max(1, health.expected_sample_count) * 100.0), 2
            )
        else:
            health.availability_pct = None

        # Duplicates.
        duplicates = sum(
            1 for a, b in zip(times, times[1:], strict=False) if abs(b - a) <= dup_tolerance_s
        )
        if duplicates:
            health.issues.append("DUPLICATE_FRAMES" if entry.stream_type in CAMERA_TYPES else "DUPLICATE_SAMPLES")

        # Monotonicity: check the *original* order, not the sorted copy.
        original = [s.t for s in entry.samples]
        if any(b < a for a, b in zip(original, original[1:], strict=False)):
            health.issues.append("NON_MONOTONIC_TIMESTAMPS")

        # Gaps.
        gaps = [(b - a) * 1000.0 for a, b in zip(times, times[1:], strict=False)]
        nominal_gap_ms = 1000.0 / float(entry.nominal_rate_hz) if entry.nominal_rate_hz else None
        health.max_gap_ms = round(max(gaps), 3) if gaps else 0.0
        gap_limit = max(max_gap_ms, (nominal_gap_ms * 2.5) if nominal_gap_ms else max_gap_ms)
        if health.max_gap_ms and health.max_gap_ms > gap_limit:
            issue = {
                "camera": "FRAME_TIMESTAMP_GAP",
                "lidar": "LIDAR_TIMESTAMP_GAP",
                "radar": "RADAR_TIMESTAMP_GAP",
                "gps": "GPS_GAP",
                "imu": "IMU_GAP",
                "can": "CAN_DATA_GAP",
                "localization": "LOCALIZATION_GAP",
            }.get(entry.stream_type, "TIMESTAMP_GAP")
            health.issues.append(issue)

        # Dropped frames.
        if (
            health.availability_pct is not None
            and health.availability_pct < min_availability
            and entry.stream_type in CAMERA_TYPES
        ):
            health.issues.append("DROPPED_FRAMES")
        elif health.availability_pct is not None and health.availability_pct < min_availability:
            health.issues.append("MISSING_SAMPLES")

        # Frozen stream.
        if nominal_gap_ms:
            run = _longest_identical_run([s.signature for s in entry.samples])
            frozen_span_s = run * (nominal_gap_ms / 1000.0)
            if run > 1 and frozen_span_s >= frozen_seconds:
                health.issues.append("FROZEN_VIDEO" if entry.stream_type in CAMERA_TYPES else "FROZEN_STREAM")

        # Offset and drift against the master clock.
        view = timeline.views.get(MasterTimeline.stream_key(entry))
        if view is not None and timeline.master_key:
            master = timeline.views.get(timeline.master_key)
            if master is not None and master.times and view.times:
                health.sync_offset_ms = round((view.times[0] - master.times[0]) * 1000.0, 3)

        # Quality score: availability, minus penalties for each defect class.
        score = (health.availability_pct if health.availability_pct is not None else 100.0) / 100.0
        penalties = {
            "FROZEN_VIDEO": 0.5,
            "FROZEN_STREAM": 0.5,
            "NON_MONOTONIC_TIMESTAMPS": 0.3,
            "DROPPED_FRAMES": 0.2,
            "MISSING_SAMPLES": 0.2,
            "DUPLICATE_FRAMES": 0.1,
            "DUPLICATE_SAMPLES": 0.1,
        }
        for issue in health.issues:
            score -= penalties.get(issue, 0.1)
        health.quality_score = round(max(0.0, min(1.0, score)), 3)

        if not health.issues:
            health.status = "ok"
        elif requirement == StreamRequirement.REQUIRED and health.quality_score < 0.5:
            health.status = "blocking"
        else:
            health.status = "degraded"

        results.append(health)

    return results


def build_synchronization_report(
    manifest: list[StreamManifestEntry],
    config: SensorConfiguration,
    timeline: MasterTimeline,
) -> SynchronizationReport:
    cfg = _sync_config()
    max_camera_offset = float(cfg.get("max_camera_offset_ms", 50.0))
    max_telemetry_offset = float(cfg.get("max_telemetry_offset_ms", 100.0))

    health = analyse_streams(manifest, config, timeline)

    camera_offsets = [
        abs(h.sync_offset_ms) for h in health if h.stream_type in CAMERA_TYPES and h.sync_offset_ms is not None
    ]
    telemetry_offsets = [
        abs(h.sync_offset_ms) for h in health if h.stream_type in TELEMETRY_TYPES and h.sync_offset_ms is not None
    ]
    gaps = [h.max_gap_ms for h in health if h.max_gap_ms is not None]

    drift: dict[str, float] = {}
    for entry in manifest:
        if not entry.present or not entry.samples:
            continue
        value = _estimate_drift_ms_per_s([s.t for s in entry.samples], entry.nominal_rate_hz)
        if value is not None and abs(value) > 0.5:
            drift[_stream_key(entry)] = value

    issues: list[str] = []
    for h in health:
        for issue in h.issues:
            issues.append(f"{h.key}: {issue}")

    max_cam = max(camera_offsets) if camera_offsets else None
    max_tel = max(telemetry_offsets) if telemetry_offsets else None
    if max_cam is not None and max_cam > max_camera_offset:
        issues.append(f"CAMERA_DESYNC: {max_cam:.1f} ms exceeds the {max_camera_offset:.0f} ms budget")
    if max_tel is not None and max_tel > max_telemetry_offset:
        issues.append(
            f"CAMERA_TO_TELEMETRY_DESYNC: {max_tel:.1f} ms exceeds the {max_telemetry_offset:.0f} ms budget"
        )

    considered = [h for h in health if h.requirement != StreamRequirement.IGNORE]
    blocking = [h for h in considered if h.status == "blocking"]
    scores = [h.quality_score for h in considered if h.quality_score is not None]
    mean_quality = sum(scores) / len(scores) if scores else 0.0

    if not timeline.available:
        quality = "unusable"
    elif blocking:
        quality = "unusable"
    elif mean_quality >= 0.9 and not issues:
        quality = "good"
    elif mean_quality >= 0.75:
        quality = "acceptable"
    else:
        quality = "degraded"

    # Confidence reflects both mean stream quality and whether the master clock
    # was one of the preferred references rather than an arbitrary fallback.
    master_bonus = 0.05 if timeline.master_key in timeline.master_preference else -0.10
    confidence = round(max(0.0, min(1.0, mean_quality + master_bonus)), 3)

    return SynchronizationReport(
        master_stream=timeline.master_key,
        master_start_t=round(timeline.start_t, 4),
        master_end_t=round(timeline.end_t, 4),
        stream_health=health,
        max_camera_offset_ms=max_cam,
        max_telemetry_offset_ms=max_tel,
        max_gap_ms=max(gaps) if gaps else None,
        drift_ms_per_s=drift,
        quality=quality,
        confidence=confidence,
        issues=issues,
        has_blocking_errors=bool(blocking) or not timeline.available,
    )
