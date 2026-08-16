"""Deterministic synthetic dataset generator and DEMO-MODE adapter.

Why this exists
---------------
Two legitimate uses, and no others:

* **DEMO MODE** - so the dashboard can be demonstrated and evaluated before an
  approved data source is connected. The adapter refuses to run while the
  platform is in production mode, and every record it produces is stamped
  ``is_synthetic=True`` and surfaced with a DEMO badge.
* **The golden dataset** - ``tests/golden_dataset`` is generated from this
  module, so the fixtures are reproducible from source rather than being opaque
  committed blobs.

The generator is fully deterministic for a given seed: the same seed always
produces byte-identical documents.

Nothing here ever substitutes for a real source. If Data Scout is unavailable
during a production run, the run fails with an actionable message - it does not
fall back to this module.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.connectors.base import ConnectionStatus, DemoDataRefused
from backend.connectors.local_files import LocalFilesAdapter
from backend.settings import get_settings

SCHEMA_ID = "av-scout-local-event/1.0"
BASE_TIME = datetime(2026, 8, 1, 6, 0, 0, tzinfo=timezone.utc)

JUNCTION_HALF_M = 10.0
APPROACH_LEN_M = 250.0
STOP_LINE_Y = -12.0
DT = 0.1


# ---------------------------------------------------------------------------
# Event specification
# ---------------------------------------------------------------------------
@dataclass
class EventSpec:
    """Declarative description of one synthetic event.

    ``faults`` names the defects deliberately injected so the golden dataset
    exercises every blocking rule and every difficult-case branch.
    """

    index: int
    country_code: str = "DE"
    country_name: str = "Germany"
    city: str = "Munich"
    region: str = "Bavaria"
    road_type: str = "urban"
    lane_count: int = 3
    intersection: str = "four_way"
    complexity: str = "moderate"
    traffic_control: list[str] = field(default_factory=lambda: ["traffic_light"])
    signal_state: str = "green"
    weather: str = "clear"
    lighting: str = "day"
    turn: str = "straight"  # straight | left | right
    stop_type: str = "none"  # none | rolling | full
    v0: float = 13.9
    bus_relation: str = "object_ahead"
    bus_subtype: str = "city_bus"
    scenario_tags: list[str] = field(default_factory=lambda: ["BUS_AHEAD_SAME_LANE"])
    maneuver: str = "straight"
    reference_data: bool = True
    lane_offset_m: float = 0.4
    faults: set[str] = field(default_factory=set)
    difficulty: str = "easy"

    @property
    def event_id(self) -> str:
        return f"EVT-{self.index:04d}"

    @property
    def session_id(self) -> str:
        return f"SESS-{(self.index // 3) + 1:03d}"


def default_specs() -> list[EventSpec]:
    """The golden dataset: easy cases, hard cases, and every blocking rule.

    Kept explicit rather than randomised so a reviewer can read exactly which
    defect each fixture is supposed to provoke.
    """
    return [
        # --- easy, clean cases -------------------------------------------
        EventSpec(index=1, difficulty="easy"),
        EventSpec(
            index=2,
            city="Berlin",
            region="Berlin",
            lane_count=2,
            turn="right",
            maneuver="right_turn",
            stop_type="full",
            scenario_tags=["BUS_AHEAD_SAME_LANE", "BUS_INTERSECTION"],
            difficulty="easy",
        ),
        EventSpec(
            index=3,
            city="Hamburg",
            region="Hamburg",
            road_type="residential",
            lane_count=1,
            intersection="three_way_t_junction",
            complexity="simple",
            traffic_control=["stop_sign"],
            signal_state="unknown",
            stop_type="full",
            turn="left",
            maneuver="left_turn",
            bus_relation="object_left_adjacent",
            scenario_tags=["BUS_ADJACENT_LANE"],
            difficulty="easy",
        ),
        # --- weather / lighting variety ----------------------------------
        EventSpec(
            index=4,
            city="Cologne",
            region="North Rhine-Westphalia",
            weather="rain",
            lighting="dusk",
            stop_type="rolling",
            scenario_tags=["BUS_AHEAD_SAME_LANE", "BUS_CLOSE_DISTANCE"],
            difficulty="moderate",
        ),
        EventSpec(
            index=5,
            city="Munich",
            weather="fog",
            lighting="night",
            v0=9.7,
            bus_subtype="articulated_bus",
            scenario_tags=["BUS_AHEAD_SAME_LANE", "ARTICULATED_BUS"],
            difficulty="moderate",
        ),
        # --- road-type variety --------------------------------------------
        EventSpec(
            index=6,
            city="Stuttgart",
            region="Baden-Wurttemberg",
            road_type="autobahn",
            lane_count=4,
            intersection="highway_ramp",
            complexity="complex",
            traffic_control=["uncontrolled"],
            signal_state="unknown",
            v0=27.8,
            bus_relation="object_right_adjacent",
            scenario_tags=["BUS_ADJACENT_LANE", "BUS_HIGH_RELATIVE_SPEED"],
            difficulty="moderate",
        ),
        EventSpec(
            index=7,
            city="Frankfurt",
            region="Hesse",
            road_type="roundabout",
            intersection="roundabout",
            complexity="complex",
            traffic_control=["yield_sign"],
            signal_state="unknown",
            turn="right",
            maneuver="right_turn",
            stop_type="rolling",
            scenario_tags=["BUS_CROSSING_EGO_PATH"],
            difficulty="hard",
        ),
        # --- non-DE events, to prove country filtering is authoritative ----
        EventSpec(
            index=8,
            country_code="FR",
            country_name="France",
            city="Lyon",
            region="Auvergne-Rhone-Alpes",
            difficulty="easy",
        ),
        EventSpec(
            index=9,
            country_code="NL",
            country_name="Netherlands",
            city="Rotterdam",
            region="South Holland",
            road_type="bus_lane",
            scenario_tags=["BUS_AT_STOP"],
            bus_relation="object_at_bus_stop",
            difficulty="easy",
        ),
        # --- deliberate defects: blocking ----------------------------------
        EventSpec(
            index=10,
            city="Dresden",
            region="Saxony",
            faults={"missing_vehicle_state"},
            difficulty="blocking",
        ),
        EventSpec(
            index=11,
            city="Leipzig",
            region="Saxony",
            faults={"reversed_eval_window"},
            difficulty="blocking",
        ),
        EventSpec(
            index=12,
            city="Nuremberg",
            region="Bavaria",
            faults={"self_intersecting_polygon"},
            difficulty="blocking",
        ),
        EventSpec(
            index=13,
            city="Bremen",
            region="Bremen",
            faults={"two_point_polygon"},
            difficulty="blocking",
        ),
        EventSpec(
            index=14,
            city="Hannover",
            region="Lower Saxony",
            faults={"missing_country_code"},
            difficulty="blocking",
        ),
        # --- deliberate defects: sensor / sync -----------------------------
        EventSpec(
            index=15,
            city="Essen",
            region="North Rhine-Westphalia",
            faults={"frozen_camera", "timestamp_gap"},
            difficulty="hard",
        ),
        EventSpec(
            index=16,
            city="Dortmund",
            region="North Rhine-Westphalia",
            faults={"duplicate_timestamps", "non_monotonic"},
            difficulty="hard",
        ),
        EventSpec(
            index=17,
            city="Duesseldorf",
            region="North Rhine-Westphalia",
            faults={"low_localization", "map_misalignment"},
            lane_offset_m=7.5,
            difficulty="hard",
        ),
        # --- deliberate defects: perception / tracking ---------------------
        EventSpec(
            index=18,
            city="Munich",
            faults={"track_id_switch"},
            scenario_tags=["BUS_AHEAD_SAME_LANE", "BUS_OCCLUDED"],
            difficulty="hard",
        ),
        EventSpec(
            index=19,
            city="Berlin",
            region="Berlin",
            faults={"missed_detection"},
            difficulty="hard",
        ),
        EventSpec(
            index=20,
            city="Berlin",
            region="Berlin",
            faults={"wrong_classification", "track_fragmentation"},
            difficulty="hard",
        ),
        # --- edge cases -----------------------------------------------------
        EventSpec(
            index=21,
            city="Kiel",
            region="Schleswig-Holstein",
            intersection="railroad_crossing",
            complexity="simple",
            traffic_control=["railroad_control"],
            signal_state="unknown",
            stop_type="full",
            faults={"no_map_context"},
            difficulty="hard",
        ),
        EventSpec(
            index=22,
            city="Munich",
            reference_data=False,
            scenario_tags=["BUS_AHEAD_SAME_LANE", "MULTIPLE_BUSES"],
            difficulty="moderate",
        ),
        EventSpec(
            index=23,
            city="Augsburg",
            region="Bavaria",
            faults={"illegal_signal_transition"},
            scenario_tags=["BUS_AHEAD_SAME_LANE", "BUS_SIGNAL_INTERACTION"],
            difficulty="hard",
        ),
        EventSpec(
            index=24,
            city="Mannheim",
            region="Baden-Wurttemberg",
            faults={"false_positive"},
            difficulty="hard",
        ),
        EventSpec(
            index=25,
            city="Karlsruhe",
            region="Baden-Wurttemberg",
            stop_type="full",
            faults={"signal_metadata_mismatch"},
            signal_state="green",
            difficulty="hard",
        ),
    ]


# ---------------------------------------------------------------------------
# Trajectory simulation
# ---------------------------------------------------------------------------
def _simulate_poses(spec: EventSpec) -> list[dict[str, Any]]:
    """Integrate a plausible approach-through-junction trajectory.

    Local metric frame: x=east, y=north, junction centred on the origin, ego
    approaching from the south.
    """
    target_turn = {"left": math.pi / 2, "right": -math.pi / 2}.get(spec.turn, 0.0)
    if spec.stop_type == "full":
        v_min, hold_s = 0.0, 2.4
    elif spec.stop_type == "rolling":
        v_min, hold_s = 0.9, 0.0
    else:
        v_min, hold_s = spec.v0 * 0.75, 0.0

    brake_start = max(35.0, spec.v0 * 4.5)
    x, y, heading, v, t = 0.0, -APPROACH_LEN_M, math.pi / 2, spec.v0, 0.0
    turned, stop_timer = 0.0, 0.0
    released = hold_s <= 0.0
    state = "approach"
    poses: list[dict[str, Any]] = []
    quality = 0.35 if "low_localization" in spec.faults else 0.96

    while t <= 90.0:
        inside = abs(x) <= JUNCTION_HALF_M and abs(y) <= JUNCTION_HALF_M
        if state == "approach" and inside:
            state = "inside"
        elif state == "inside" and not inside:
            state = "exited"

        if state == "approach" and not (hold_s > 0.0 and released):
            remaining = (-JUNCTION_HALF_M) - y
            if remaining > brake_start:
                v_target = spec.v0
            else:
                v_target = v_min + (spec.v0 - v_min) * max(0.0, remaining / brake_start)
            if hold_s > 0.0 and remaining <= 2.0:
                v_target = 0.0
        else:
            # Once the hold is served (or none was required) the ego resumes.
            v_target = spec.v0

        if not released and v <= 0.25 and state == "approach":
            stop_timer += DT
            if stop_timer >= hold_s:
                released = True

        dv = max(-3.0 * DT, min(2.0 * DT, v_target - v))
        accel = dv / DT
        v = max(0.0, v + dv)

        if state in ("inside", "exited") and abs(turned) < abs(target_turn) and target_turn != 0.0:
            step = math.copysign(min(1.1 * DT, abs(target_turn - turned)), target_turn)
            heading += step
            turned += step

        x += v * math.cos(heading) * DT
        y += v * math.sin(heading) * DT

        poses.append(
            {
                "t": round(t, 3),
                "x_m": round(x, 3),
                "y_m": round(y, 3),
                "heading_rad": round(heading, 5),
                "speed_mps": round(v, 3),
                "accel_mps2": round(accel, 3),
                "steering_rad": round(math.copysign(0.22, target_turn) if state == "inside" and target_turn else 0.0, 4),
                "localization_quality": quality,
            }
        )

        t = round(t + DT, 3)
        if state == "exited" and math.hypot(x, y) > 45.0:
            break

    return poses


def _resample_line(poses: list[dict[str, Any]], offset_m: float, step: int = 4) -> list[list[float]]:
    """Lane centreline derived from the driven path, laterally offset.

    The final pose is always included: a polyline that stops short of the
    trajectory would leave the last few metres with no nearby centreline, which
    the map-alignment check would (correctly) report as a large offset.
    """
    indices = list(range(0, len(poses), step))
    if indices and indices[-1] != len(poses) - 1:
        indices.append(len(poses) - 1)

    out: list[list[float]] = []
    for i in indices:
        pose = poses[i]
        heading = float(pose["heading_rad"])
        nx, ny = -math.sin(heading), math.cos(heading)
        out.append([round(pose["x_m"] + nx * offset_m, 3), round(pose["y_m"] + ny * offset_m, 3)])
    return out


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------
def _signature(stream: str, index: int) -> str:
    return hashlib.sha256(f"{stream}:{index}".encode()).hexdigest()[:12]


def _make_stream(
    stream_type: str,
    duration: float,
    rate_hz: float,
    *,
    camera_position: str | None = None,
    frozen_window: tuple[float, float] | None = None,
    gap_window: tuple[float, float] | None = None,
    duplicate_count: int = 0,
    non_monotonic: bool = False,
) -> dict[str, Any]:
    period = 1.0 / rate_hz
    samples: list[dict[str, Any]] = []
    n = int(duration / period)
    for i in range(n + 1):
        t = round(i * period, 4)
        if gap_window and gap_window[0] <= t <= gap_window[1]:
            continue
        if frozen_window and frozen_window[0] <= t <= frozen_window[1]:
            signature = _signature(stream_type, int(frozen_window[0] * rate_hz))
        else:
            signature = _signature(stream_type, i)
        samples.append({"t": t, "signature": signature})

    for k in range(duplicate_count):
        idx = min(len(samples) - 1, 40 + k)
        if idx >= 0:
            samples.insert(idx + 1, dict(samples[idx]))

    if non_monotonic and len(samples) > 60:
        samples[50], samples[52] = samples[52], samples[50]

    return {
        "stream_type": stream_type,
        "camera_position": camera_position,
        "present": True,
        "start_t": samples[0]["t"] if samples else 0.0,
        "end_t": samples[-1]["t"] if samples else 0.0,
        "nominal_rate_hz": rate_hz,
        "sample_count": len(samples),
        "samples": samples,
    }


def _build_streams(spec: EventSpec, duration: float) -> list[dict[str, Any]]:
    streams: list[dict[str, Any]] = []

    if "missing_vehicle_state" not in spec.faults:
        streams.append(_make_stream("vehicle_state", duration, 10.0))
    else:
        streams.append(
            {
                "stream_type": "vehicle_state",
                "camera_position": None,
                "present": False,
                "sample_count": 0,
                "samples": [],
                "notes": "Stream absent from the source export.",
            }
        )

    streams.append(
        _make_stream(
            "localization",
            duration,
            10.0,
            duplicate_count=5 if "duplicate_timestamps" in spec.faults else 0,
        )
    )
    streams.append(
        _make_stream(
            "camera",
            duration,
            10.0,
            camera_position="front_main",
            frozen_window=(8.0, 13.0) if "frozen_camera" in spec.faults else None,
            gap_window=(10.0, 11.6) if "timestamp_gap" in spec.faults else None,
        )
    )
    streams.append(_make_stream("camera", duration, 10.0, camera_position="front_wide"))
    streams.append(_make_stream("can", duration, 20.0, non_monotonic="non_monotonic" in spec.faults))
    streams.append(_make_stream("perception", duration, 10.0))
    streams.append(_make_stream("map", duration, 1.0))
    return streams


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------
def _build_detections(spec: EventSpec, poses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bus (and signal) detections consistent with the simulated approach."""
    detections: list[dict[str, Any]] = []
    duration = poses[-1]["t"] if poses else 0.0
    first_visible_t = round(duration * 0.18, 2)

    base_track = f"T-{spec.index:03d}-BUS"
    switch_at = duration * 0.62
    frag_bounds = (duration * 0.35, duration * 0.55)

    for pose in poses:
        t = float(pose["t"])
        if t < first_visible_t:
            continue
        # The lead bus sits ~35 m ahead at the start and closes as ego brakes.
        distance = max(6.0, 42.0 - (t - first_visible_t) * 1.15)
        confidence = 0.62 if t < first_visible_t + 1.5 else 0.93

        track_id = base_track
        if "track_id_switch" in spec.faults and t >= switch_at:
            track_id = f"{base_track}-B"
        if "track_fragmentation" in spec.faults:
            if frag_bounds[0] <= t <= frag_bounds[1]:
                continue
            track_id = f"{base_track}-{'A' if t < frag_bounds[0] else 'C'}"

        perception_missing = "missed_detection" in spec.faults and duration * 0.40 <= t <= duration * 0.58
        object_type = "bus"
        if "wrong_classification" in spec.faults and duration * 0.45 <= t <= duration * 0.60:
            object_type = "truck"

        box_h = min(0.62, 6.0 / max(distance, 4.0))
        box_w = box_h * 0.95
        # The bus enters from the left edge, so it is visible before it is
        # wholly in frame - that separation is what first_visible / full_view
        # are meant to capture.
        entering = t < first_visible_t + 1.3
        box_x = (-0.04 if entering else 0.5 - box_w / 2)
        bbox = {
            "x": round(box_x, 4),
            "y": round(0.52 - box_h / 2, 4),
            "w": round(box_w, 4),
            "h": round(box_h, 4),
        }

        if not perception_missing:
            detections.append(
                {
                    "t": round(t, 2),
                    "camera": "front_main",
                    "source": "perception",
                    "object_type": object_type,
                    "object_subtype": spec.bus_subtype if object_type == "bus" else None,
                    "track_id": track_id,
                    "bounding_box": bbox,
                    "distance_m": round(distance, 2),
                    "velocity_mps": round(max(0.0, float(pose["speed_mps"]) - 1.2), 2),
                    "lane_relation": spec.bus_relation,
                    "confidence": confidence,
                    "model_version": "perception-sim-1.0",
                }
            )

        if spec.reference_data:
            detections.append(
                {
                    "t": round(t, 2),
                    "camera": "front_main",
                    "source": "reference",
                    "object_type": "bus",
                    "object_subtype": spec.bus_subtype,
                    "track_id": f"{base_track}-REF",
                    "bounding_box": bbox,
                    "distance_m": round(distance, 2),
                    "velocity_mps": round(max(0.0, float(pose["speed_mps"]) - 1.2), 2),
                    "lane_relation": spec.bus_relation,
                    "confidence": 1.0,
                    "model_version": "reference-annotation",
                }
            )

    if "false_positive" in spec.faults:
        # A contiguous burst, not scattered samples: an isolated frame is
        # detector jitter, and the engine is right to ignore it.
        ghost_start, ghost_end = duration * 0.30, duration * 0.45
        for pose in poses:
            t = float(pose["t"])
            if not ghost_start <= t <= ghost_end:
                continue
            detections.append(
                {
                    "t": round(t, 2),
                    "camera": "front_wide",
                    "source": "perception",
                    "object_type": "bus",
                    "track_id": f"{base_track}-GHOST",
                    "bounding_box": {"x": 0.05, "y": 0.5, "w": 0.08, "h": 0.1},
                    "distance_m": 62.0,
                    "confidence": 0.55,
                    "model_version": "perception-sim-1.0",
                }
            )

    if "traffic_light" in spec.traffic_control:
        for pose in poses[:: max(1, len(poses) // 25)]:
            t = float(pose["t"])
            detections.append(
                {
                    "t": round(t, 2),
                    "camera": "front_main",
                    "source": "perception",
                    "object_type": "traffic_light",
                    "track_id": f"T-{spec.index:03d}-TL",
                    "bounding_box": {"x": 0.58, "y": 0.22, "w": 0.03, "h": 0.07},
                    "state": _signal_state_at(spec, t, duration),
                    "distance_m": round(max(8.0, abs(float(pose["y_m"]) - STOP_LINE_Y)), 2),
                    "confidence": 0.9,
                    "model_version": "perception-sim-1.0",
                }
            )

    return detections


def _signal_state_at(spec: EventSpec, t: float, duration: float) -> str:
    """Signal state over time, so state-transition rules have something to check.

    German signals include the red-yellow phase, which the region signal model
    expects. ``illegal_signal_transition`` deliberately jumps green -> red with
    no yellow in between.
    """
    if "illegal_signal_transition" in spec.faults:
        return "green" if t < duration * 0.5 else "red"
    if spec.signal_state != "green":
        return spec.signal_state
    if spec.country_code in ("DE", "AT"):
        if t < duration * 0.45:
            return "red"
        if t < duration * 0.55:
            return "red_yellow"
        return "green"
    if t < duration * 0.5:
        return "red"
    return "green"


# ---------------------------------------------------------------------------
# Map context
# ---------------------------------------------------------------------------
def _junction_polygon(spec: EventSpec) -> list[list[float]]:
    h = JUNCTION_HALF_M
    if "self_intersecting_polygon" in spec.faults:
        # Deliberate bow-tie: provokes GEOMETRY_POLYGON_VALID.
        return [[-h, -h], [h, h], [h, -h], [-h, h], [-h, -h]]
    if "two_point_polygon" in spec.faults:
        return [[-h, -h], [h, h]]
    return [[-h, -h], [h, -h], [h, h], [-h, h], [-h, -h]]


def _build_map_context(spec: EventSpec, poses: list[dict[str, Any]]) -> dict[str, Any] | None:
    if "no_map_context" in spec.faults:
        return None

    map_version = f"hdmap-2026.06-{spec.country_code.lower()}"
    features: list[dict[str, Any]] = [
        {
            "feature_id": f"J-{spec.index:03d}",
            "feature_type": "junction",
            "geometry": {"type": "Polygon", "coordinates": [_junction_polygon(spec)]},
            "attributes": {
                "intersection_type": spec.intersection,
                "branch_count": 4 if spec.intersection == "four_way" else 3,
                "lane_count": spec.lane_count,
                "traffic_control": spec.traffic_control,
                "turn_options": 3,
                "road_type": spec.road_type,
            },
            "map_version": map_version,
            "confidence": 0.95,
        },
        # A nearby decoy so junction ranking has a real choice to make.
        {
            "feature_id": f"J-{spec.index:03d}-DECOY",
            "feature_type": "junction",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[50.0, -150.0], [70.0, -150.0], [70.0, -130.0], [50.0, -130.0], [50.0, -150.0]]],
            },
            "attributes": {
                "intersection_type": "three_way_t_junction",
                "branch_count": 3,
                "lane_count": 2,
                "traffic_control": ["uncontrolled"],
                "turn_options": 2,
                "road_type": "residential",
            },
            "map_version": map_version,
            "confidence": 0.9,
        },
        {
            "feature_id": f"LC-{spec.index:03d}-EGO",
            "feature_type": "lane_centerline",
            "geometry": {"type": "LineString", "coordinates": _resample_line(poses, spec.lane_offset_m)},
            "attributes": {"lane_id": "ego", "lane_configuration": "ego_lane", "direction": "forward"},
            "map_version": map_version,
            "confidence": 0.93,
        },
        {
            "feature_id": f"LC-{spec.index:03d}-ADJ",
            "feature_type": "lane_centerline",
            "geometry": {"type": "LineString", "coordinates": _resample_line(poses, spec.lane_offset_m + 3.5)},
            "attributes": {"lane_id": "adjacent_right", "lane_configuration": "right_adjacent_lane"},
            "map_version": map_version,
            "confidence": 0.9,
        },
        {
            "feature_id": f"SL-{spec.index:03d}",
            "feature_type": "stop_line",
            "geometry": {"type": "LineString", "coordinates": [[-5.25, STOP_LINE_Y], [5.25, STOP_LINE_Y]]},
            "attributes": {"applies_to": "ego"},
            "map_version": map_version,
            "confidence": 0.92,
        },
    ]

    if "traffic_light" in spec.traffic_control:
        features.append(
            {
                "feature_id": f"TL-{spec.index:03d}",
                "feature_type": "traffic_signal",
                "geometry": {"type": "Point", "coordinates": [4.8, STOP_LINE_Y - 0.5]},
                "attributes": {"controls_lane": "ego", "signal_group": "A"},
                "map_version": map_version,
                "confidence": 0.94,
            }
        )

    return {"map_version": map_version, "features": features}


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------
def build_document(spec: EventSpec) -> dict[str, Any]:
    """Build one ``av-scout-local-event/1.0`` document from a spec."""
    poses = _simulate_poses(spec)
    duration = round(poses[-1]["t"], 2) if poses else 0.0

    event_time = BASE_TIME + timedelta(days=spec.index % 15, hours=(spec.index * 3) % 18, minutes=spec.index * 7)
    if spec.lighting == "night":
        event_time = event_time.replace(hour=22)
    elif spec.lighting == "dusk":
        event_time = event_time.replace(hour=20)

    window_start = event_time - timedelta(seconds=duration * 0.8)
    window_end = event_time + timedelta(seconds=duration * 0.4)
    if "reversed_eval_window" in spec.faults:
        window_start, window_end = window_end, window_start

    # Source-shaped field names on purpose: schema discovery and the field
    # mapping editor must have something real to resolve.
    metadata: dict[str, Any] = {
        "event": spec.event_id,
        "session": spec.session_id,
        "job_id": f"JOB-{(spec.index // 5) + 1:03d}",
        "country_name": spec.country_name,
        "country_code": spec.country_code,
        "state": spec.region,
        "town": spec.city,
        "timestamp": event_time.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "duration_s": duration,
        "road_class": spec.road_type,
        "num_lanes": spec.lane_count,
        "junction_type": spec.intersection,
        "complexity": spec.complexity,
        "traffic_control": spec.traffic_control,
        "signal_state": spec.signal_state,
        "weather_condition": spec.weather,
        "time_of_day": spec.lighting,
        "class": ["bus"],
        "bus_type": spec.bus_subtype,
        "tags": spec.scenario_tags,
        "maneuver": spec.maneuver,
        "project": "av-validation-eu",
        "dataset": f"eu-{spec.country_code.lower()}-2026q3",
        "dataset_version": "1.4.0",
        "campaign": "summer-2026",
        "vehicle_build": "rig-b7",
        "stack_version": "av-stack-9.2.1",
        "hd_map_version": f"hdmap-2026.06-{spec.country_code.lower()}",
        "event_type": "junction_interaction",
    }

    if "missing_country_code" in spec.faults:
        metadata.pop("country_code")
        metadata.pop("country_name")
        # Only a filename hint remains - deliberately non-authoritative.
        metadata["source_filename"] = f"{spec.country_name.lower()}_{spec.event_id}.mcap"

    map_context = _build_map_context(spec, poses)
    detections = _build_detections(spec, poses)

    # The recorded signal state should agree with what was actually observed,
    # so that a disagreement in a fixture is deliberate rather than incidental.
    # `signal_metadata_mismatch` makes one event disagree on purpose, which is
    # what routes it to senior review.
    observed_states = [d["state"] for d in detections if d["object_type"] == "traffic_light" and d.get("state")]
    if observed_states and "signal_metadata_mismatch" not in spec.faults:
        # Most frequent state, ties broken alphabetically. `max(set(...))` would
        # be order-dependent: Python randomises string hashing per process, so a
        # tie would resolve differently on every run.
        counts = Counter(observed_states)
        metadata["signal_state"] = min(counts.items(), key=lambda item: (-item[1], item[0]))[0]

    document: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "generator": "backend.connectors.synthetic",
        "difficulty": spec.difficulty,
        "injected_faults": sorted(spec.faults),
        "is_synthetic": True,
        "metadata": metadata,
        "streams": _build_streams(spec, duration),
        "poses": poses,
        "detections": detections,
        "reference_data_available": spec.reference_data,
        "source_start_t": 0.0,
        "source_end_t": duration,
    }
    if map_context is not None:
        document["map_context"] = map_context
    return document


def generate_documents(specs: list[EventSpec] | None = None) -> list[dict[str, Any]]:
    return [build_document(spec) for spec in (specs or default_specs())]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class SyntheticAdapter(LocalFilesAdapter):
    """In-memory synthetic source. Refused unless the platform is in DEMO MODE."""

    name = "synthetic"
    display_name = "Synthetic Test Dataset (DEMO ONLY)"
    is_synthetic = True
    demo_only = True

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        super().__init__(settings)
        self._documents: list[dict[str, Any]] | None = None

    def _guard(self) -> None:
        if not get_settings().is_demo_mode:
            raise DemoDataRefused(
                "synthetic adapter requested while AV_MODE=production",
                user_message=(
                    "Synthetic data is refused while the platform is in production mode.\n\n"
                    "Production mode never substitutes fake data for an unavailable source. "
                    "Switch AV_MODE to 'demo' explicitly, or connect an approved data source."
                ),
            )

    def authenticate(self) -> None:
        self._guard()
        self._authenticated = True

    def test_connection(self) -> ConnectionStatus:
        try:
            self._guard()
        except DemoDataRefused as exc:
            return ConnectionStatus(connected=False, status="DEMO_ONLY", message=exc.user_message)
        index = self._build_index()
        return ConnectionStatus(
            connected=True,
            status="CONNECTED",
            message=f"DEMO MODE - {len(index)} synthetic events generated in memory.",
            latency_ms=0.0,
            api_version="synthetic",
            schema_version=SCHEMA_ID,
            permissions=["read"],
        )

    def _build_index(self) -> dict[str, Any]:
        self._guard()
        if self._index is not None:
            return self._index
        if self._documents is None:
            self._documents = generate_documents()
        index: dict[str, Any] = {}
        for document in self._documents:
            event_id = str(document["metadata"].get("event", ""))
            index[event_id] = None  # in-memory: no backing file
            self._raw_cache[event_id] = document
        self._index = index
        return index
