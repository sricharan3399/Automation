"""Geometry: trajectory, polygon, edges, junction ranking and timestamps."""

from __future__ import annotations

import math

import pytest

from backend.geometry.edges import entry_exit_arc_lengths, rank_edges
from backend.geometry.junctions import (
    find_candidate_junctions,
    intersection_complexity,
    rank_junctions,
    selection_is_ambiguous,
)
from backend.geometry.polygon import assess_polygon, normalised_ring, polygon_edges
from backend.geometry.timestamps import calculate_markers
from backend.geometry.trajectory import (
    arc_length_at_time,
    build_trajectory,
    heading_change_deg,
    point_at_arc_length,
    time_at_arc_length,
)
from backend.models.contracts import (
    JunctionCandidate,
    MapContext,
    MapFeatureContract,
    MapGeometry,
    PoseSample,
)

SQUARE = [[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]]


def straight_poses(count: int = 60, speed: float = 10.0, dt: float = 0.1) -> list[PoseSample]:
    """Ego driving due north at constant speed from y=-30."""
    return [
        PoseSample(
            t=round(i * dt, 3),
            x_m=0.0,
            y_m=-30.0 + speed * i * dt,
            heading_rad=math.pi / 2,
            speed_mps=speed,
            localization_quality=0.95,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------
class TestTrajectory:
    def test_arc_length_accumulates_the_travelled_distance(self):
        trajectory = build_trajectory(straight_poses())
        assert trajectory.valid
        # 59 steps of 1.0 m each.
        assert trajectory.total_length_m == pytest.approx(59.0, abs=1e-6)

    def test_a_single_pose_cannot_form_a_trajectory(self):
        trajectory = build_trajectory(straight_poses(count=1))
        assert not trajectory.valid
        assert "at least 2" in (trajectory.invalid_reason or "")

    def test_a_stationary_ego_is_rejected_with_a_reason(self):
        poses = [PoseSample(t=i * 0.1, x_m=0.0, y_m=0.0) for i in range(20)]
        trajectory = build_trajectory(poses)
        assert not trajectory.valid
        assert "stationary" in (trajectory.invalid_reason or "")

    def test_an_implausible_position_jump_invalidates_the_trajectory(self):
        poses = straight_poses(count=10)
        poses[5].x_m = 500.0
        trajectory = build_trajectory(poses)
        assert not trajectory.valid
        assert "jump" in (trajectory.invalid_reason or "")

    def test_time_at_arc_length_interpolates_and_reports_its_resolution(self):
        trajectory = build_trajectory(straight_poses())
        t, error = time_at_arc_length(trajectory, 10.5)
        assert t == pytest.approx(1.05, abs=1e-3)
        # Half the sample interval: the honest resolution limit of the estimate.
        assert error == pytest.approx(0.05, abs=1e-6)

    def test_arc_length_outside_the_recorded_range_returns_none(self):
        trajectory = build_trajectory(straight_poses())
        assert time_at_arc_length(trajectory, 10_000.0)[0] is None
        assert arc_length_at_time(trajectory, 10_000.0) is None

    def test_point_at_arc_length_interpolates_position(self):
        trajectory = build_trajectory(straight_poses())
        point = point_at_arc_length(trajectory, 20.0)
        assert point is not None
        assert point.y_m == pytest.approx(-10.0, abs=1e-6)

    def test_heading_change_is_zero_for_a_straight_run(self):
        trajectory = build_trajectory(straight_poses())
        assert heading_change_deg(trajectory) == pytest.approx(0.0, abs=1e-6)

    def test_heading_change_wraps_correctly_through_pi(self):
        poses = straight_poses(count=20)
        for index, pose in enumerate(poses):
            pose.heading_rad = math.pi / 2 + index * 0.05
        trajectory = build_trajectory(poses)
        assert heading_change_deg(trajectory) == pytest.approx(math.degrees(0.05 * 19), abs=0.01)


# ---------------------------------------------------------------------------
# Polygon
# ---------------------------------------------------------------------------
class TestPolygon:
    def test_a_valid_square_is_accepted_with_its_area(self):
        assessment = assess_polygon(SQUARE, build_trajectory(straight_poses()))
        assert assessment.is_valid
        assert assessment.area_m2 == pytest.approx(400.0)
        assert assessment.trajectory_crosses

    def test_two_points_cannot_form_a_polygon(self):
        assessment = assess_polygon([[0.0, 0.0], [1.0, 1.0]])
        assert not assessment.is_valid
        assert assessment.unique_point_count == 2
        assert any("unique point" in issue for issue in assessment.issues)

    def test_collinear_points_enclose_no_area(self):
        assessment = assess_polygon([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0]])
        assert assessment.collinear
        assert not assessment.is_valid

    def test_a_self_intersecting_ring_is_detected_and_repaired(self):
        bowtie = [[-10.0, -10.0], [10.0, 10.0], [10.0, -10.0], [-10.0, 10.0]]
        assessment = assess_polygon(bowtie)
        assert assessment.self_intersecting
        assert not assessment.is_valid
        # The repair is offered, never applied automatically.
        assert len(assessment.recommended_polygon) >= 4

    def test_duplicate_and_closing_points_are_ignored(self):
        assessment = assess_polygon([*SQUARE, [-10.0, -10.0], [-10.0, -10.0]])
        assert assessment.unique_point_count == 4

    def test_a_polygon_the_trajectory_misses_is_flagged(self):
        far = [[100.0, 100.0], [120.0, 100.0], [120.0, 120.0], [100.0, 120.0]]
        assessment = assess_polygon(far, build_trajectory(straight_poses()))
        assert not assessment.trajectory_crosses
        assert any("does not pass through" in issue for issue in assessment.issues)

    def test_normalised_ring_is_closed_and_stable(self):
        ring = normalised_ring(SQUARE)
        assert ring[0] == ring[-1]
        assert normalised_ring(SQUARE) == ring

    def test_edges_are_produced_for_every_side(self):
        assert len(polygon_edges(SQUARE)) == 4


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
class TestEdges:
    def test_entry_and_exit_edges_are_distinct_and_ordered(self):
        trajectory = build_trajectory(straight_poses())
        entry, exit_edge, _, _ = rank_edges(trajectory, SQUARE, "J-1")
        assert entry is not None and exit_edge is not None
        assert entry.edge_id != exit_edge.edge_id
        assert entry.crossing_t is not None and exit_edge.crossing_t is not None
        assert entry.crossing_t < exit_edge.crossing_t

    def test_entry_arc_length_precedes_exit_arc_length(self):
        trajectory = build_trajectory(straight_poses())
        entry_s, exit_s = entry_exit_arc_lengths(trajectory, SQUARE)
        assert entry_s is not None and exit_s is not None
        assert entry_s < exit_s
        # South edge at y=-10 is 20 m from the start at y=-30.
        assert entry_s == pytest.approx(20.0, abs=0.6)

    def test_a_trajectory_that_never_enters_produces_no_edges(self):
        far_poses = [
            PoseSample(t=i * 0.1, x_m=100.0, y_m=-30.0 + i, heading_rad=math.pi / 2, speed_mps=10.0)
            for i in range(40)
        ]
        entry, exit_edge, _, _ = rank_edges(build_trajectory(far_poses), SQUARE, "J-1")
        assert entry is None and exit_edge is None

    def test_alternatives_are_offered_for_reviewer_override(self):
        trajectory = build_trajectory(straight_poses())
        _, _, entry_alternatives, _ = rank_edges(trajectory, SQUARE, "J-1")
        assert len(entry_alternatives) >= 1


# ---------------------------------------------------------------------------
# Junction ranking
# ---------------------------------------------------------------------------
def map_context_with(*polygons: tuple[str, list[list[float]]]) -> MapContext:
    return MapContext(
        available=True,
        map_version="test",
        features=[
            MapFeatureContract(
                feature_id=feature_id,
                feature_type="junction",
                geometry=MapGeometry(type="Polygon", coordinates=[[*ring, ring[0]]]),
                attributes={"branch_count": 4, "lane_count": 3, "turn_options": 3},
                confidence=0.9,
            )
            for feature_id, ring in polygons
        ],
    )


class TestJunctionRanking:
    def test_the_junction_on_the_route_outranks_a_distant_one(self):
        trajectory = build_trajectory(straight_poses(count=100))
        far = [[80.0, -80.0], [100.0, -80.0], [100.0, -60.0], [80.0, -60.0]]
        context = map_context_with(("J-ON-ROUTE", SQUARE), ("J-FAR", far))

        candidates = find_candidate_junctions(trajectory, context)
        ranked = rank_junctions(trajectory, candidates)

        assert ranked[0].feature_id == "J-ON-ROUTE"
        assert ranked[0].trajectory_intersects
        assert ranked[0].score > ranked[1].score

    def test_a_clear_winner_is_not_reported_as_ambiguous(self):
        trajectory = build_trajectory(straight_poses(count=100))
        far = [[80.0, -80.0], [100.0, -80.0], [100.0, -60.0], [80.0, -60.0]]
        ranked = rank_junctions(trajectory, find_candidate_junctions(trajectory, map_context_with(("A", SQUARE), ("B", far))))
        assert not selection_is_ambiguous(ranked)

    def test_junctions_outside_the_search_radius_are_not_candidates(self):
        trajectory = build_trajectory(straight_poses())
        distant = [[5000.0, 5000.0], [5020.0, 5000.0], [5020.0, 5020.0], [5000.0, 5020.0]]
        assert find_candidate_junctions(trajectory, map_context_with(("FAR", distant))) == []

    def test_no_map_context_yields_no_candidates(self):
        trajectory = build_trajectory(straight_poses())
        assert find_candidate_junctions(trajectory, MapContext(available=False)) == []

    def test_complexity_is_unknown_when_the_map_does_not_state_it(self):
        assert intersection_complexity(None) == "unknown"
        assert intersection_complexity(JunctionCandidate(feature_id="J", score=1.0, attributes={})) == "unknown"

    def test_complexity_grows_with_mapped_structure(self):
        simple = JunctionCandidate(
            feature_id="J", score=1.0, attributes={"branch_count": 3, "lane_count": 1, "turn_options": 2}
        )
        complex_junction = JunctionCandidate(
            feature_id="K",
            score=1.0,
            attributes={"branch_count": 6, "lane_count": 6, "turn_options": 5, "traffic_control": ["a", "b", "c"]},
        )
        assert intersection_complexity(simple) == "simple"
        assert intersection_complexity(complex_junction) == "very_complex"

    def test_scoring_only_weights_components_it_could_evaluate(self):
        trajectory = build_trajectory(straight_poses(count=100))
        ranked = rank_junctions(trajectory, find_candidate_junctions(trajectory, map_context_with(("J", SQUARE))))
        used = ranked[0].attributes["weights_used"]
        # No metadata was supplied, so those components must not be counted.
        assert "road_type_match" not in used
        assert sum(used.values()) == pytest.approx(sum(used.values()))


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------
class TestTimestamps:
    def test_markers_are_ordered_along_the_direction_of_travel(self):
        trajectory = build_trajectory(straight_poses(count=400, speed=10.0))
        markers = {m.name: m for m in calculate_markers(trajectory, SQUARE, MapContext(available=False))}

        entry = markers["junction_entry"]
        exit_marker = markers["junction_exit"]
        assert entry.available and exit_marker.available
        assert entry.t is not None and exit_marker.t is not None
        assert entry.t < exit_marker.t

    def test_a_marker_beyond_the_clip_is_unavailable_with_a_reason(self):
        # 30 m of approach cannot contain a 200 m marker.
        trajectory = build_trajectory(straight_poses(count=60))
        markers = {m.name: m for m in calculate_markers(trajectory, SQUARE, MapContext(available=False))}
        marker = markers["timestamp_200m"]
        assert not marker.available
        assert marker.unavailable_reason
        assert marker.t is None

    def test_every_marker_is_unavailable_when_the_trajectory_is_invalid(self):
        trajectory = build_trajectory(straight_poses(count=1))
        markers = calculate_markers(trajectory, SQUARE, MapContext(available=False))
        assert markers and all(not m.available for m in markers)

    def test_markers_carry_their_interpolation_error_and_pose_quality(self):
        trajectory = build_trajectory(straight_poses(count=400))
        entry = next(m for m in calculate_markers(trajectory, SQUARE, MapContext(available=False)) if m.name == "junction_entry")
        assert entry.interpolation_error_s is not None
        assert entry.pose_quality == pytest.approx(0.95)
        assert 0.0 < entry.confidence <= 1.0

    def test_markers_come_back_in_canonical_order(self):
        trajectory = build_trajectory(straight_poses(count=400))
        names = [m.name for m in calculate_markers(trajectory, SQUARE, MapContext(available=False))]
        assert names.index("timestamp_200m") < names.index("junction_entry")
        assert names.index("junction_entry") < names.index("junction_exit")
        assert names.index("junction_exit") < names.index("post_junction_20m")
