"""Master timeline construction and sensor/synchronisation fault detection."""

from __future__ import annotations

import pytest

from backend.models.contracts import (
    PoseSample,
    SensorConfiguration,
    StreamManifestEntry,
    StreamRequirement,
    StreamRequirementSpec,
    StreamSample,
)
from backend.synchronization.checks import analyse_streams, build_synchronization_report
from backend.synchronization.timeline import MasterTimeline


def stream(
    stream_type: str,
    *,
    camera: str | None = None,
    rate: float = 10.0,
    duration: float = 10.0,
    frozen_from: float | None = None,
    gap: tuple[float, float] | None = None,
    duplicates: int = 0,
    non_monotonic: bool = False,
    present: bool = True,
) -> StreamManifestEntry:
    period = 1.0 / rate
    samples: list[StreamSample] = []
    for index in range(int(duration / period) + 1):
        t = round(index * period, 4)
        if gap and gap[0] <= t <= gap[1]:
            continue
        signature = f"sig-{int(frozen_from * rate)}" if (frozen_from is not None and t >= frozen_from) else f"sig-{index}"
        samples.append(StreamSample(t=t, signature=signature))

    for offset in range(duplicates):
        index = min(len(samples) - 1, 10 + offset)
        samples.insert(index + 1, StreamSample(t=samples[index].t, signature=samples[index].signature))

    if non_monotonic and len(samples) > 20:
        samples[10], samples[12] = samples[12], samples[10]

    return StreamManifestEntry(
        stream_type=stream_type,
        camera_position=camera,
        present=present,
        start_t=samples[0].t if samples else 0.0,
        end_t=samples[-1].t if samples else 0.0,
        nominal_rate_hz=rate,
        sample_count=len(samples),
        samples=samples,
    )


def poses(duration: float = 10.0, dt: float = 0.1) -> list[PoseSample]:
    return [
        PoseSample(t=round(i * dt, 3), x_m=0.0, y_m=i * dt * 10.0, speed_mps=10.0, localization_quality=0.95)
        for i in range(int(duration / dt) + 1)
    ]


def config(**requirements: str) -> SensorConfiguration:
    return SensorConfiguration(
        streams=[
            StreamRequirementSpec(stream_type=name, requirement=StreamRequirement(value))
            for name, value in requirements.items()
        ]
    )


def issues_for(health, key: str) -> list[str]:
    for entry in health:
        if entry.key == key:
            return entry.issues
    return []


class TestMasterTimeline:
    def test_the_preferred_stream_becomes_the_master_clock(self):
        timeline = MasterTimeline([stream("vehicle_state"), stream("camera", camera="front_main")], poses())
        assert timeline.master_key == "vehicle_state"

    def test_a_preferred_stream_with_no_samples_is_not_chosen(self):
        empty = StreamManifestEntry(stream_type="vehicle_state", present=False, samples=[], sample_count=0)
        timeline = MasterTimeline([empty, stream("camera", camera="front_main")], poses())
        assert timeline.master_key != "vehicle_state"

    def test_pose_interpolation_between_samples(self):
        timeline = MasterTimeline([stream("vehicle_state")], poses())
        pose = timeline.pose_at(0.55)
        assert pose is not None
        assert pose.y_m == pytest.approx(5.5, abs=0.01)

    def test_no_extrapolation_beyond_the_recorded_range(self):
        timeline = MasterTimeline([stream("vehicle_state")], poses())
        assert timeline.pose_at(9999.0) is None
        assert timeline.pose_at(-5.0) is None

    def test_frame_lookup_respects_the_tolerance(self):
        timeline = MasterTimeline([stream("camera", camera="front_main")], poses())
        # 5.05 s falls midway between two 10 Hz samples.
        assert timeline.frame_at(5.05, "front_main") is not None
        assert timeline.frame_at(5.05, "front_main", tolerance_s=0.0001) is None
        assert timeline.frame_at(5.0, "rear") is None

    def test_object_lookup_windows_by_time(self):
        from backend.models.contracts import DetectionContract

        detections = [DetectionContract(t=t / 10, object_type="bus") for t in range(100)]
        timeline = MasterTimeline([stream("vehicle_state")], poses(), detections)
        assert len(timeline.object_at(5.0, tolerance_s=0.15)) >= 2
        assert timeline.object_at(5.0, object_type="truck") == []


class TestStreamHealth:
    def test_a_healthy_stream_reports_no_issues(self):
        streams = [stream("vehicle_state")]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(vehicle_state="required"), timeline)
        assert issues_for(health, "vehicle_state") == []

    def test_a_missing_required_stream_blocks(self):
        streams = [stream("camera", camera="front_main")]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(vehicle_state="required"), timeline)
        entry = next(h for h in health if h.stream_type == "vehicle_state")
        assert not entry.present
        assert entry.status == "blocking"
        assert "MISSING_SENSOR_STREAM" in entry.issues

    def test_a_frozen_camera_is_detected(self):
        streams = [stream("camera", camera="front_main", frozen_from=2.0, duration=10.0)]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(), timeline)
        assert "FROZEN_VIDEO" in issues_for(health, "camera:front_main")

    def test_a_timestamp_gap_is_detected(self):
        streams = [stream("camera", camera="front_main", gap=(3.0, 5.0))]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(), timeline)
        assert "FRAME_TIMESTAMP_GAP" in issues_for(health, "camera:front_main")

    def test_dropped_frames_are_detected_against_the_declared_rate(self):
        streams = [stream("camera", camera="front_main", gap=(2.0, 6.0))]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(), timeline)
        assert "DROPPED_FRAMES" in issues_for(health, "camera:front_main")

    def test_duplicate_samples_are_detected(self):
        streams = [stream("localization", duplicates=4)]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(), timeline)
        assert "DUPLICATE_SAMPLES" in issues_for(health, "localization")

    def test_non_monotonic_timestamps_are_detected(self):
        streams = [stream("can", rate=20.0, non_monotonic=True)]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(streams, config(), timeline)
        assert "NON_MONOTONIC_TIMESTAMPS" in issues_for(health, "can")

    def test_ignored_streams_are_not_analysed(self):
        streams = [stream("camera", camera="front_main", frozen_from=1.0)]
        timeline = MasterTimeline(streams, poses())
        health = analyse_streams(
            streams,
            SensorConfiguration(
                streams=[
                    StreamRequirementSpec(
                        stream_type="camera", camera_position="front_main", requirement=StreamRequirement.IGNORE
                    )
                ]
            ),
            timeline,
        )
        assert health == []


class TestSynchronizationReport:
    def test_a_clean_event_is_good_quality_and_not_blocking(self):
        streams = [stream("vehicle_state"), stream("camera", camera="front_main")]
        timeline = MasterTimeline(streams, poses())
        report = build_synchronization_report(streams, config(vehicle_state="required"), timeline)
        assert report.quality == "good"
        assert not report.has_blocking_errors
        assert report.confidence > 0.9

    def test_a_missing_required_stream_makes_the_event_unusable(self):
        streams = [stream("camera", camera="front_main")]
        timeline = MasterTimeline(streams, poses())
        report = build_synchronization_report(streams, config(vehicle_state="required"), timeline)
        assert report.has_blocking_errors
        assert report.quality == "unusable"

    def test_issues_are_reported_per_stream(self):
        streams = [stream("vehicle_state"), stream("camera", camera="front_main", frozen_from=2.0)]
        timeline = MasterTimeline(streams, poses())
        report = build_synchronization_report(streams, config(vehicle_state="required"), timeline)
        assert any("FROZEN_VIDEO" in issue for issue in report.issues)

    def test_an_empty_manifest_cannot_produce_a_timeline(self):
        timeline = MasterTimeline([], [])
        report = build_synchronization_report([], config(), timeline)
        assert report.has_blocking_errors
        assert report.quality == "unusable"
