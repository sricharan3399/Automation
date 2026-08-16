"""The master timeline.

Every stream carries its own clock. This module normalises them onto one
timeline and exposes the four accessors the rest of the pipeline uses::

    frame_at(t)   -> nearest camera sample on a given camera
    signal_at(t)  -> nearest sample on a named non-camera stream
    pose_at(t)    -> interpolated ego pose
    object_at(t)  -> detections active at t

All accessors return ``None``/empty rather than extrapolating outside the data.
Interpolating past the end of a stream would invent measurements, and every
downstream timestamp would inherit that invention.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Any

from backend.models.contracts import (
    DetectionContract,
    PoseSample,
    StreamManifestEntry,
    StreamSample,
)
from backend.settings import get_settings

DEFAULT_MASTER_PREFERENCE = ["vehicle_state", "localization", "can", "camera_front_main"]


@dataclass
class StreamView:
    """One stream, offset-corrected onto the master timeline."""

    key: str
    stream_type: str
    camera_position: str | None
    times: list[float]
    samples: list[StreamSample]
    nominal_rate_hz: float | None
    applied_offset_ms: float

    def nearest(self, t: float, max_distance_s: float | None = None) -> StreamSample | None:
        if not self.times:
            return None
        index = bisect.bisect_left(self.times, t)
        candidates: list[int] = []
        if index < len(self.times):
            candidates.append(index)
        if index > 0:
            candidates.append(index - 1)
        best = min(candidates, key=lambda i: abs(self.times[i] - t))
        if max_distance_s is not None and abs(self.times[best] - t) > max_distance_s:
            return None
        return self.samples[best]


class MasterTimeline:
    """Offset-corrected view over every stream, pose and detection of an event."""

    def __init__(
        self,
        streams: list[StreamManifestEntry],
        poses: list[PoseSample],
        detections: list[DetectionContract] | None = None,
        master_preference: list[str] | None = None,
    ) -> None:
        self.raw_streams = streams
        self.poses = sorted((p for p in poses), key=lambda p: p.t)
        self._pose_times = [p.t for p in self.poses]
        self.detections = sorted(detections or [], key=lambda d: d.t)
        self._detection_times = [d.t for d in self.detections]
        self.master_preference = master_preference or list(
            get_settings().section("synchronization").get("master_stream_preference", DEFAULT_MASTER_PREFERENCE)
        )

        self.master_key = self._select_master()
        self.views: dict[str, StreamView] = {}
        self._build_views()

        self.start_t, self.end_t = self._bounds()

    # -- construction ----------------------------------------------------
    @staticmethod
    def stream_key(entry: StreamManifestEntry) -> str:
        return f"{entry.stream_type}_{entry.camera_position}" if entry.camera_position else entry.stream_type

    def _usable(self) -> list[StreamManifestEntry]:
        return [s for s in self.raw_streams if s.present and s.samples]

    def _select_master(self) -> str | None:
        """Choose the reference clock.

        Preference order is configuration, not a hard-coded assumption, and the
        first *usable* candidate wins - a preferred stream with no samples is
        not silently accepted as the master.
        """
        usable = {self.stream_key(s): s for s in self._usable()}
        for preferred in self.master_preference:
            if preferred in usable:
                return preferred
        # Fall back to the densest stream so the timeline still exists.
        if usable:
            return max(usable, key=lambda k: len(usable[k].samples))
        return None

    def _build_views(self) -> None:
        master_start = 0.0
        for entry in self._usable():
            if self.stream_key(entry) == self.master_key:
                master_start = min(s.t for s in entry.samples)
                break

        for entry in self._usable():
            key = self.stream_key(entry)
            offset_ms = entry.declared_offset_ms or 0.0
            offset_s = offset_ms / 1000.0
            samples = sorted(entry.samples, key=lambda s: s.t)
            corrected = [
                StreamSample(t=s.t - offset_s, signature=s.signature, payload=s.payload) for s in samples
            ]
            self.views[key] = StreamView(
                key=key,
                stream_type=entry.stream_type,
                camera_position=entry.camera_position,
                times=[s.t for s in corrected],
                samples=corrected,
                nominal_rate_hz=entry.nominal_rate_hz,
                applied_offset_ms=offset_ms,
            )
        self._master_start = master_start

    def _bounds(self) -> tuple[float, float]:
        starts: list[float] = []
        ends: list[float] = []
        for view in self.views.values():
            if view.times:
                starts.append(view.times[0])
                ends.append(view.times[-1])
        if self._pose_times:
            starts.append(self._pose_times[0])
            ends.append(self._pose_times[-1])
        if not starts:
            return 0.0, 0.0
        return min(starts), max(ends)

    @property
    def available(self) -> bool:
        return bool(self.views) or bool(self.poses)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_t - self.start_t)

    def in_range(self, t: float) -> bool:
        return self.start_t - 1e-6 <= t <= self.end_t + 1e-6

    # -- accessors -------------------------------------------------------
    def frame_at(self, t: float, camera: str = "front_main", tolerance_s: float = 0.2) -> StreamSample | None:
        view = self.views.get(f"camera_{camera}")
        if view is None:
            return None
        return view.nearest(t, max_distance_s=tolerance_s)

    def signal_at(self, t: float, stream: str, tolerance_s: float = 0.2) -> StreamSample | None:
        view = self.views.get(stream)
        if view is None:
            return None
        return view.nearest(t, max_distance_s=tolerance_s)

    def pose_at(self, t: float) -> PoseSample | None:
        """Linear interpolation between the two bracketing poses.

        Returns ``None`` outside the recorded range instead of extrapolating.
        """
        if not self.poses or not self.in_pose_range(t):
            return None
        index = bisect.bisect_left(self._pose_times, t)
        if index == 0:
            return self.poses[0]
        if index >= len(self.poses):
            return self.poses[-1]

        before, after = self.poses[index - 1], self.poses[index]
        span = after.t - before.t
        if span <= 0:
            return before
        ratio = (t - before.t) / span

        def lerp(a: float | None, b: float | None) -> float | None:
            if a is None or b is None:
                return a if b is None else b
            return a + (b - a) * ratio

        heading = before.heading_rad
        if before.heading_rad is not None and after.heading_rad is not None:
            delta = math.atan2(
                math.sin(after.heading_rad - before.heading_rad),
                math.cos(after.heading_rad - before.heading_rad),
            )
            heading = before.heading_rad + delta * ratio

        return PoseSample(
            t=t,
            x_m=before.x_m + (after.x_m - before.x_m) * ratio,
            y_m=before.y_m + (after.y_m - before.y_m) * ratio,
            heading_rad=heading,
            speed_mps=lerp(before.speed_mps, after.speed_mps),
            accel_mps2=lerp(before.accel_mps2, after.accel_mps2),
            steering_rad=lerp(before.steering_rad, after.steering_rad),
            localization_quality=lerp(before.localization_quality, after.localization_quality),
        )

    def in_pose_range(self, t: float) -> bool:
        if not self._pose_times:
            return False
        return self._pose_times[0] - 1e-6 <= t <= self._pose_times[-1] + 1e-6

    def object_at(self, t: float, tolerance_s: float = 0.15, object_type: str | None = None) -> list[DetectionContract]:
        if not self.detections:
            return []
        low = bisect.bisect_left(self._detection_times, t - tolerance_s)
        high = bisect.bisect_right(self._detection_times, t + tolerance_s)
        window = self.detections[low:high]
        if object_type:
            window = [d for d in window if d.object_type == object_type]
        return window

    # -- introspection ---------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "master_stream": self.master_key,
            "start_t": round(self.start_t, 4),
            "end_t": round(self.end_t, 4),
            "duration_s": round(self.duration_s, 4),
            "streams": sorted(self.views),
            "pose_count": len(self.poses),
            "detection_count": len(self.detections),
        }
