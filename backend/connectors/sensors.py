"""Sensor-stream resolution.

Retrieves stream manifests from the event source (or, when configured, a
dedicated sensor store) and annotates each stream with the requirement the
tester set on the Sensor Configuration page.

A stream the tester marked *Required* that the source does not provide is
reported as ``present=False`` with an explicit reason - it is never fabricated
and never silently downgraded to optional.
"""

from __future__ import annotations

from backend.models.contracts import (
    SensorConfiguration,
    StreamManifestEntry,
    StreamRequirement,
)

CAMERA_STREAM = "camera"


def annotate_requirements(
    manifest: list[StreamManifestEntry],
    config: SensorConfiguration,
) -> list[tuple[StreamManifestEntry, StreamRequirement]]:
    """Pair each supplied stream with its configured requirement."""
    return [(entry, config.requirement_for(entry.stream_type, entry.camera_position)) for entry in manifest]


def missing_required_streams(
    manifest: list[StreamManifestEntry],
    config: SensorConfiguration,
) -> list[str]:
    """Required stream keys that the source did not deliver at all.

    A stream that appears in the manifest with ``present=False`` counts as
    missing: the source explicitly told us it is not there.
    """
    supplied = {
        (f"{e.stream_type}:{e.camera_position}" if e.camera_position else e.stream_type)
        for e in manifest
        if e.present and e.sample_count > 0
    }
    return [key for key in config.required_keys() if key not in supplied]


def declared_stream_keys(manifest: list[StreamManifestEntry]) -> list[str]:
    return [(f"{e.stream_type}:{e.camera_position}" if e.camera_position else e.stream_type) for e in manifest]


def camera_streams(manifest: list[StreamManifestEntry]) -> list[StreamManifestEntry]:
    return [e for e in manifest if e.stream_type == CAMERA_STREAM]


def telemetry_streams(manifest: list[StreamManifestEntry]) -> list[StreamManifestEntry]:
    telemetry = {"vehicle_state", "can", "imu", "gps", "localization"}
    return [e for e in manifest if e.stream_type in telemetry]
