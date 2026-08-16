"""Per-event pipeline.

Executes the stages in order and produces one :class:`ReviewPackage`:

    metadata -> country/scenario filter -> sensor check -> synchronisation ->
    trajectory -> map context -> junction/polygon/edges/timestamps ->
    scene analysis -> behaviour -> validation -> confidence -> recommendations
    -> evidence -> review package

A blocking data error short-circuits the analytical stages: the event is routed
to data review with the reason recorded, rather than producing geometry and
findings derived from data already known to be unusable.

The processor never writes to the database and never mutates the source. It
takes inputs and returns a package; persistence is the run manager's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.behavior.analysis import analyse_behavior
from backend.connectors.base import AdapterError, DataScoutAdapter
from backend.connectors.maps import MapContextResolver
from backend.connectors.matching import evaluate
from backend.geometry.edges import rank_edges
from backend.geometry.junctions import (
    find_candidate_junctions,
    intersection_complexity,
    rank_junctions,
    selection_is_ambiguous,
)
from backend.geometry.polygon import assess_polygon
from backend.geometry.timestamps import calculate_markers
from backend.geometry.trajectory import build_trajectory
from backend.identity import (
    anonymized_event_ref,
    anonymized_job_ref,
    anonymized_session_ref,
    canonical_event_key,
)
from backend.models.contracts import (
    BehaviorAnalysis,
    EventBundle,
    GeometryResult,
    PipelineStage,
    RecordStatus,
    ReviewPackage,
    SceneAnalysis,
    ScoutQuery,
    SensorConfiguration,
    Severity,
    SynchronizationReport,
    Trajectory,
)
from backend.recommendations.confidence import aggregate_overall
from backend.recommendations.prefill import build_recommendations, field_confidence_map
from backend.synchronization.checks import build_synchronization_report
from backend.synchronization.timeline import MasterTimeline
from backend.validation.engine import abnormality_categories, run_rules
from backend.validation.registry import ValidationContext
from backend.validation.rules_geometry import max_lateral_offset
from backend.version import CONTRACT_VERSION, METHOD_VERSION, SOFTWARE_VERSION
from backend.vision.scene import analyse_scene

log = logging.getLogger(__name__)


@dataclass
class ProcessOutcome:
    """Result of processing one event."""

    event_id: str
    stage_reached: str = PipelineStage.METADATA.value
    package: ReviewPackage | None = None
    context: ValidationContext | None = None
    filtered_out: bool = False
    filter_reason: str | None = None
    error: str | None = None
    error_is_retryable: bool = False
    stages_completed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.package is not None and self.error is None


class EventProcessor:
    def __init__(
        self,
        adapter: DataScoutAdapter,
        sensor_config: SensorConfiguration,
        query: ScoutQuery,
        rule_overrides: dict[str, bool] | None = None,
        map_resolver: MapContextResolver | None = None,
        evidence_generator: Any | None = None,
        existing_reviews: dict[str, dict[str, dict[str, Any]]] | None = None,
        existing_keys: set[str] | None = None,
    ) -> None:
        self.adapter = adapter
        self.sensor_config = sensor_config
        self.query = query
        self.rule_overrides = rule_overrides or {}
        self.map_resolver = map_resolver or MapContextResolver()
        self.evidence_generator = evidence_generator
        #: canonical_event_key -> {field_name: review record}
        self.existing_reviews = existing_reviews or {}
        #: canonical event keys already present in the database, so a re-run
        #: upserts instead of inserting a duplicate.
        self.existing_keys = existing_keys if existing_keys is not None else set()

    # -- main entry point ---------------------------------------------------
    def process(self, event_id: str) -> ProcessOutcome:
        outcome = ProcessOutcome(event_id=event_id)
        try:
            bundle = self.adapter.get_event_bundle(event_id)
        except AdapterError as exc:
            outcome.error = exc.user_message
            outcome.error_is_retryable = exc.retryable
            return outcome
        except Exception as exc:  # defensive: one bad event must not kill the run
            log.exception("Unexpected error retrieving %s", event_id)
            outcome.error = f"Unexpected error retrieving this event: {exc}"
            return outcome

        outcome.stages_completed.append(PipelineStage.METADATA.value)
        metadata = bundle.metadata

        # --- country / scenario filter -------------------------------------
        matched, reason = evaluate(self.query, metadata)
        outcome.stages_completed.append(PipelineStage.COUNTRY_FILTER.value)
        outcome.stages_completed.append(PipelineStage.SCENARIO_FILTER.value)
        if not matched:
            outcome.filtered_out = True
            outcome.filter_reason = reason
            outcome.stage_reached = PipelineStage.SCENARIO_FILTER.value
            return outcome

        # --- sensors + synchronisation --------------------------------------
        timeline = MasterTimeline(bundle.streams, bundle.poses, bundle.detections)
        sync = build_synchronization_report(bundle.streams, self.sensor_config, timeline)
        outcome.stages_completed.append(PipelineStage.SENSOR_CHECK.value)
        outcome.stages_completed.append(PipelineStage.SYNCHRONIZATION.value)
        outcome.stage_reached = PipelineStage.SYNCHRONIZATION.value

        if sync.has_blocking_errors:
            # Route to data review. Analytical stages are deliberately skipped:
            # deriving geometry from data already known to be unusable would
            # manufacture findings nobody should act on.
            ctx = self._context(bundle, sync, Trajectory(), GeometryResult(), SceneAnalysis(), BehaviorAnalysis())
            package = self._finalise(
                ctx,
                blocked=True,
                blocked_reason="; ".join(sync.issues) or "Required sensor data is missing or unusable.",
            )
            outcome.package = package
            outcome.context = ctx
            return outcome

        # --- trajectory ------------------------------------------------------
        trajectory = build_trajectory(bundle.poses)

        # --- map context -----------------------------------------------------
        map_context = self.map_resolver.resolve(event_id, bundle.map_context)
        bundle.map_context = map_context
        outcome.stages_completed.append(PipelineStage.MAP_CONTEXT.value)
        outcome.stage_reached = PipelineStage.MAP_CONTEXT.value

        # --- scene analysis ---------------------------------------------------
        scene = analyse_scene(bundle.detections, bundle.reference_data_available)
        outcome.stages_completed.append(PipelineStage.SCENE_ANALYSIS.value)

        # --- geometry ---------------------------------------------------------
        geometry, ambiguous = self._geometry(bundle, trajectory, scene)
        outcome.stage_reached = PipelineStage.SCENE_ANALYSIS.value

        # --- behaviour --------------------------------------------------------
        behavior = analyse_behavior(trajectory, geometry.markers)
        outcome.stages_completed.append(PipelineStage.BEHAVIOR_ANALYSIS.value)

        ctx = self._context(bundle, sync, trajectory, geometry, scene, behavior)
        ctx.extras["junction_ambiguous"] = ambiguous
        ctx.extras["intersection_complexity"] = intersection_complexity(geometry.target_junction)

        # Map alignment is measured once here and reused by the rule and the
        # confidence engine, so they can never disagree.
        offset = max_lateral_offset(ctx)
        geometry.map_alignment_offset_m = offset
        if offset is not None:
            limit = 5.0
            geometry.map_alignment_confidence = round(max(0.0, min(1.0, 1.0 - offset / max(limit, 0.1))), 4)

        package = self._finalise(ctx)
        outcome.stages_completed.append(PipelineStage.VALIDATION.value)
        outcome.stages_completed.append(PipelineStage.EVIDENCE.value)
        outcome.stage_reached = PipelineStage.EVIDENCE.value
        outcome.package = package
        outcome.context = ctx
        return outcome

    # -- stages -------------------------------------------------------------
    def _geometry(
        self, bundle: EventBundle, trajectory: Trajectory, scene: SceneAnalysis
    ) -> tuple[GeometryResult, bool]:
        metadata = bundle.metadata
        if not trajectory.valid:
            return (
                GeometryResult(
                    available=False,
                    unavailable_reason=trajectory.invalid_reason or "The ego trajectory is unusable.",
                    markers=calculate_markers(trajectory, None, bundle.map_context),
                ),
                False,
            )
        if not bundle.map_context.available:
            return (
                GeometryResult(
                    available=False,
                    unavailable_reason=bundle.map_context.unavailable_reason
                    or "No map context is available for this event.",
                    markers=calculate_markers(trajectory, None, bundle.map_context),
                ),
                False,
            )

        event_t_rel = self._event_t_rel(metadata, bundle)
        candidates = find_candidate_junctions(trajectory, bundle.map_context)
        ranked = rank_junctions(trajectory, candidates, metadata, event_t_rel)
        if not ranked:
            return (
                GeometryResult(
                    available=False,
                    unavailable_reason=(
                        "No mapped junction was found near the ego trajectory within the configured "
                        "search radius."
                    ),
                    markers=calculate_markers(trajectory, None, bundle.map_context),
                ),
                False,
            )

        top = ranked[0]
        ambiguous = selection_is_ambiguous(ranked)
        polygon = assess_polygon(top.polygon, trajectory)
        usable_polygon = top.polygon if polygon.is_valid else (polygon.recommended_polygon or [])

        entry, exit_edge, entry_alts, exit_alts = rank_edges(trajectory, usable_polygon, top.feature_id)
        markers = calculate_markers(
            trajectory,
            usable_polygon or None,
            bundle.map_context,
            origin=metadata.evaluation_start or metadata.event_time,
            map_confidence=top.map_alignment_confidence or None,
            first_visible_t=scene.first_visible_t,
            full_view_t=scene.full_view_t,
        )

        return (
            GeometryResult(
                target_junction=top,
                alternatives=ranked[1:4],
                polygon=polygon,
                entry_edge=entry,
                exit_edge=exit_edge,
                entry_alternatives=entry_alts,
                exit_alternatives=exit_alts,
                markers=markers,
                map_alignment_confidence=top.map_alignment_confidence,
                available=True,
            ),
            ambiguous,
        )

    @staticmethod
    def _event_t_rel(metadata: Any, bundle: EventBundle) -> float | None:
        """Event time expressed on the clip's relative timeline."""
        origin = metadata.evaluation_start
        if origin is None or metadata.event_time is None:
            return None
        delta = (metadata.event_time - origin).total_seconds()
        end = bundle.source_end_t
        if end is not None and (delta < -1.0 or delta > end + 1.0):
            return None
        return delta

    def _context(
        self,
        bundle: EventBundle,
        sync: SynchronizationReport,
        trajectory: Trajectory,
        geometry: GeometryResult,
        scene: SceneAnalysis,
        behavior: BehaviorAnalysis,
    ) -> ValidationContext:
        metadata = bundle.metadata
        session_ref = anonymized_session_ref(metadata.session_id)
        junction_ref = geometry.target_junction.feature_id if geometry.target_junction else None
        key = canonical_event_key(session_ref, metadata.event_type, metadata.event_time, junction_ref)

        return ValidationContext(
            metadata=metadata,
            bundle=bundle,
            sensor_config=self.sensor_config,
            sync=sync,
            trajectory=trajectory,
            geometry=geometry,
            scene=scene,
            behavior=behavior,
            canonical_event_key=key,
            duplicate_exists=key in self.existing_keys,
            origin=metadata.evaluation_start or metadata.event_time,
            query_country_code=self.query.country_code,
        )

    # -- finalisation --------------------------------------------------------
    def _finalise(
        self,
        ctx: ValidationContext,
        blocked: bool = False,
        blocked_reason: str | None = None,
    ) -> ReviewPackage:
        report = run_rules(ctx, self.rule_overrides)
        reviews = self.existing_reviews.get(ctx.canonical_event_key, {})
        recommendations = build_recommendations(ctx, reviews)

        confidences = field_confidence_map(recommendations)
        overall = aggregate_overall(confidences)

        blocking = [o for o in report.failures if o.severity == Severity.BLOCKING]
        export_blocking = report.export_blocking
        needs_review = any(o.requires_review for o in report.failures)
        senior = any(r.status == RecordStatus.SENIOR_REVIEW_REQUIRED for r in recommendations)

        if blocked or any(o.blocks_processing for o in report.failures):
            status = RecordStatus.BLOCKED_DATA_ERROR
        elif senior:
            status = RecordStatus.SENIOR_REVIEW_REQUIRED
        elif export_blocking or needs_review:
            status = RecordStatus.REVIEW_REQUIRED
        elif all(
            r.status in (RecordStatus.AUTO_PREPARED, RecordStatus.CONFIRMED_BY_TESTER)
            for r in recommendations
        ):
            status = RecordStatus.AUTO_PREPARED
        else:
            status = RecordStatus.REVIEW_REQUIRED

        metadata = ctx.metadata
        package = ReviewPackage(
            canonical_event_key=ctx.canonical_event_key,
            anonymized_event_ref=anonymized_event_ref(metadata.event_id),
            anonymized_session_ref=anonymized_session_ref(metadata.session_id),
            anonymized_job_ref=anonymized_job_ref(metadata.job_ref),
            metadata=metadata,
            synchronization=ctx.sync,
            trajectory_summary={
                "valid": ctx.trajectory.valid,
                "invalid_reason": ctx.trajectory.invalid_reason,
                "total_length_m": round(ctx.trajectory.total_length_m, 3),
                "duration_s": round(ctx.trajectory.duration_s, 3),
                "point_count": len(ctx.trajectory.points),
                "localization_quality": ctx.trajectory.localization_quality,
            },
            geometry=ctx.geometry,
            scene=ctx.scene,
            behavior=ctx.behavior,
            validation=report,
            recommendations=recommendations,
            abnormality_categories=abnormality_categories(report),
            overall_confidence=overall,
            automation_recommendation=self._recommendation_text(
                status, report, recommendations, blocked_reason
            ),
            status=status,
            blocking_error_count=len(blocking),
            review_required=status
            in (
                RecordStatus.REVIEW_REQUIRED,
                RecordStatus.SENIOR_REVIEW_REQUIRED,
                RecordStatus.BLOCKED_DATA_ERROR,
            ),
            software_version=SOFTWARE_VERSION,
            rule_version=report.rule_version,
            model_version=METHOD_VERSION,
            map_version=ctx.bundle.map_context.map_version or metadata.map_version,
            contract_version=CONTRACT_VERSION,
            is_synthetic=ctx.bundle.is_synthetic,
        )

        if self.evidence_generator is not None:
            try:
                package.evidence = self.evidence_generator.generate(ctx, package)
            except Exception as exc:
                log.exception("Evidence generation failed for %s", package.anonymized_event_ref)
                package.evidence = []
                package.automation_recommendation += f" Evidence generation failed: {exc}"

        return package

    @staticmethod
    def _recommendation_text(
        status: RecordStatus,
        report: Any,
        recommendations: list[Any],
        blocked_reason: str | None,
    ) -> str:
        if status == RecordStatus.BLOCKED_DATA_ERROR:
            return (
                "Blocked by a data error before analysis could complete: "
                + (blocked_reason or "required inputs were unusable")
                + ". Route to data review; this is not a vehicle finding."
            )

        failures = report.failures
        auto_ready = sum(1 for r in recommendations if r.auto_selected)
        total = len(recommendations)

        if status == RecordStatus.SENIOR_REVIEW_REQUIRED:
            fields = [r.field_name for r in recommendations if r.status == RecordStatus.SENIOR_REVIEW_REQUIRED]
            return (
                "Senior review required: automation disagrees with the recorded value on "
                f"safety-critical field(s) {', '.join(fields)}."
            )

        if status == RecordStatus.AUTO_PREPARED:
            return (
                f"All {total} field(s) prefilled with {auto_ready} auto-selected; no rule failed. "
                "Quick reviewer confirmation is still required before export."
            )

        categories = sorted({o.category for o in failures})
        return (
            f"{len(failures)} rule finding(s) across {', '.join(categories) or 'no category'}; "
            f"{auto_ready} of {total} fields could be auto-selected. "
            "All findings are candidates pending human review."
        )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()
