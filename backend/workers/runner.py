"""Run lifecycle management.

Owns everything between "the tester pressed RUN SCOUT" and "the outputs are on
disk":

    validate configuration -> validate connection -> freeze the configuration
    and versions -> translate filters -> estimate -> paginate -> process each
    event -> checkpoint -> validate export -> write outputs -> audit

Supports pause, resume and cancel. A cancelled or paused run saves a checkpoint
so it can be resumed without re-processing what it already did.

Dry-run does everything except write production artefacts: it tests the
connection, validates the configuration, previews the query, retrieves a small
metadata sample and reports what a real run would do.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.audit.logger import (
    ACTION_EVENT_BLOCKED,
    ACTION_EVENT_PROCESSED,
    ACTION_EXPORT_BLOCKED,
    ACTION_EXPORT_GENERATED,
    ACTION_RUN_CANCELLED,
    ACTION_RUN_CHECKPOINT,
    ACTION_RUN_COMPLETED,
    ACTION_RUN_CREATED,
    ACTION_RUN_FAILED,
    ACTION_RUN_PAUSED,
    ACTION_RUN_RESUMED,
    ACTION_RUN_STARTED,
    AuditLogger,
)
from backend.configstore import get_config_store
from backend.connectors.base import AdapterError, AdapterNotConfigured
from backend.connectors.maps import MapContextResolver
from backend.connectors.registry import ConnectionManager
from backend.database.repository import (
    existing_event_keys,
    reviews_for_keys,
    upsert_review_package,
)
from backend.database.session import session_scope
from backend.evidence.generator import EvidenceGenerator
from backend.models.contracts import (
    PIPELINE_STAGE_ORDER,
    PipelineStage,
    QueryPreview,
    RecordStatus,
    ReviewPackage,
    RunRequest,
    ScoutQuery,
)
from backend.models.orm import AutomationRun, ConfigurationProfile
from backend.pipeline.checkpoint import Checkpoint, CheckpointStore
from backend.pipeline.orchestrator import EventProcessor
from backend.reports.csv_builder import CsvBuilder, new_run_directory, write_outputs
from backend.reports.export_record import build_export_row
from backend.settings import get_settings
from backend.version import CONTRACT_VERSION, METHOD_VERSION, SOFTWARE_VERSION
from backend.workers.progress import get_progress_hub

log = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_VALIDATING = "VALIDATING"
STATUS_RUNNING = "RUNNING"
STATUS_PAUSED = "PAUSED"
STATUS_CANCELLED = "CANCELLED"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

TERMINAL_STATUSES = {STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED}


class RunError(Exception):
    """A run could not be created or started, with an actionable message."""


@dataclass
class RunController:
    run_id: str
    thread: threading.Thread | None = None
    pause_flag: threading.Event = field(default_factory=threading.Event)
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.monotonic)
    status: str = STATUS_PENDING

    def request_pause(self) -> None:
        self.pause_flag.set()

    def request_resume(self) -> None:
        self.pause_flag.clear()

    def request_cancel(self) -> None:
        self.cancel_flag.set()
        self.pause_flag.clear()


class RunManager:
    """Creates, starts and controls runs. One instance per process."""

    def __init__(self) -> None:
        self._controllers: dict[str, RunController] = {}
        self._lock = threading.Lock()
        self.checkpoints = CheckpointStore()
        self.hub = get_progress_hub()

    # ------------------------------------------------------------------
    # Preview / estimate
    # ------------------------------------------------------------------
    def preview(self, request: RunRequest) -> QueryPreview:
        """Summarise the query and estimate the record count without running."""
        with session_scope() as session:
            manager = ConnectionManager(session)
            connection_id = request.connection_id or manager.default_event_source()
            adapter = manager.adapter_for(connection_id)

            warnings: list[str] = []
            estimate: int | None = None
            exact = False
            note = ""

            try:
                adapter.authenticate()
                estimate, exact, note = adapter.estimate_count(request.query)
            except AdapterNotConfigured as exc:
                warnings.append(exc.user_message)
                note = exc.user_message
            except AdapterError as exc:
                warnings.append(exc.user_message)
                note = exc.user_message

            native: dict[str, Any] = {}
            translate = getattr(adapter, "translate_query", None)
            if callable(translate):
                try:
                    native = translate(request.query)
                except Exception:  # pragma: no cover - defensive
                    native = {}
            untranslated = getattr(adapter, "untranslated_filters", None)
            if callable(untranslated):
                missing = untranslated(request.query)
                if missing:
                    warnings.append(
                        "The source cannot express these filters natively, so they are applied "
                        "locally after retrieval: " + ", ".join(missing) + "."
                    )

            if request.query.is_empty():
                warnings.append(
                    "No filters are set, so this query matches everything the source can return."
                )
            if get_settings().is_demo_mode:
                warnings.append("The platform is in DEMO MODE. Results may come from synthetic data.")

            return QueryPreview(
                summary=self._summarise(request.query, request),
                native_query=native,
                estimated_records=estimate,
                estimate_is_exact=exact,
                estimate_note=note,
                warnings=warnings,
                adapter=adapter.name,
            )

    @staticmethod
    def _summarise(query: ScoutQuery, request: RunRequest) -> dict[str, Any]:
        def listed(values: list[str]) -> str:
            return ", ".join(values) if values else "Any"

        lanes = query.lanes
        if not lanes.lane_count_any and lanes.lane_count_exact:
            lane_text = ", ".join(str(n) for n in sorted(lanes.lane_count_exact))
        elif lanes.min_lanes is not None or lanes.max_lanes is not None:
            lane_text = f"{lanes.min_lanes or 'any'} - {lanes.max_lanes or 'any'}"
        else:
            lane_text = "Any"

        return {
            "Country": query.country or query.country_code or "Any",
            "Regions": listed(query.regions),
            "Cities": listed(query.cities),
            "Objects": listed(query.object_types),
            "Bus subtypes": listed(query.bus_subtypes),
            "Scenario tags": listed(query.scenario_tags),
            "Road types": listed(query.road_types),
            "Lane count": lane_text,
            "Lane configuration": listed(lanes.lane_configuration),
            "Intersection": listed(query.intersection_types),
            "Complexity": listed(query.intersection_complexity),
            "Traffic control": listed(query.traffic_control_entities),
            "Signal state": listed(query.traffic_light_states),
            "Manoeuvre": listed(query.vehicle_maneuvers),
            "Weather": listed(query.weather),
            "Lighting": listed(query.lighting),
            "Date range": f"{query.time_range.start_date or 'any'} to {query.time_range.end_date or 'any'}",
            "Dataset": query.dataset.dataset or "Any",
            "CSV template": request.csv_template_id,
            "Mode": "DRY RUN" if request.dry_run else "FULL RUN",
        }

    # ------------------------------------------------------------------
    # Run creation
    # ------------------------------------------------------------------
    def create_run(self, request: RunRequest, actor: str, actor_role: str) -> str:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        settings = get_settings()
        store = get_config_store()

        with session_scope() as session:
            manager = ConnectionManager(session)
            connection_id = request.connection_id or manager.default_event_source()
            profile = manager.profile(connection_id)
            if profile is None:
                raise RunError(f"Connection '{connection_id}' does not exist.")
            if not profile.enabled:
                raise RunError(
                    f"Connection '{profile.display_name}' is disabled. Enable it on the Connections "
                    "page before running."
                )

            dry_run = request.dry_run
            if settings.force_dry_run_on_first_execution and request.profile_id:
                configuration = session.scalar(
                    select(ConfigurationProfile).where(ConfigurationProfile.profile_id == request.profile_id)
                )
                if configuration is not None and configuration.executed_count == 0 and not dry_run:
                    dry_run = True
                    log.info("Forcing dry-run: profile %s has never been executed", request.profile_id)

            frozen = {
                "request": request.model_dump(mode="json"),
                "connection": {
                    "connection_id": profile.connection_id,
                    "adapter": profile.adapter,
                    "integration_type": profile.integration_type,
                    "display_name": profile.display_name,
                },
                "settings": {
                    "operating_mode": settings.operating_mode,
                    "source_access_mode": settings.source_access_mode,
                    "allow_production_submission": settings.allow_production_submission,
                    "compute_device": settings.compute_device,
                    "page_size": settings.page_size,
                    "max_events_per_run": settings.max_events_per_run,
                },
                "rule_overrides": request.rule_overrides,
                "confidence_policy": store.confidence_bands(),
                "frozen_at": datetime.now(timezone.utc).isoformat(),
            }

            run = AutomationRun(
                run_id=run_id,
                status=STATUS_PENDING,
                stage="not_started",
                dry_run=dry_run,
                profile_id=request.profile_id,
                profile_version=None,
                frozen_config=frozen,
                query_json=request.query.model_dump(mode="json"),
                connection_profile_id=profile.connection_id,
                adapter_name=profile.adapter,
                software_version=SOFTWARE_VERSION,
                contract_version=CONTRACT_VERSION,
                rule_version=store.rule_version_signature(),
                model_version=METHOD_VERSION,
                created_by=actor,
            )
            session.add(run)
            AuditLogger(session, run_id=run_id).record(
                ACTION_RUN_CREATED,
                actor=actor,
                actor_role=actor_role,
                entity_type="run",
                entity_ref=run_id,
                after={"dry_run": dry_run, "connection": profile.connection_id},
                detail=f"Run created against '{profile.display_name}'.",
            )

        with self._lock:
            self._controllers[run_id] = RunController(run_id=run_id)
        return run_id

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def start(self, run_id: str, actor: str = "local.tester", actor_role: str = "tester", resume: bool = False) -> None:
        with self._lock:
            controller = self._controllers.get(run_id)
            if controller is None:
                controller = RunController(run_id=run_id)
                self._controllers[run_id] = controller
            if controller.thread is not None and controller.thread.is_alive():
                raise RunError(f"Run {run_id} is already executing.")
            controller.cancel_flag.clear()
            controller.pause_flag.clear()
            thread = threading.Thread(
                target=self._execute,
                args=(run_id, controller, actor, actor_role, resume),
                name=f"av-run-{run_id}",
                daemon=True,
            )
            controller.thread = thread
        thread.start()

    def pause(self, run_id: str, actor: str = "local.tester") -> None:
        controller = self._controllers.get(run_id)
        if controller is None:
            raise RunError(f"Run {run_id} is not active in this process.")
        controller.request_pause()
        self._record_control(run_id, ACTION_RUN_PAUSED, actor, "Run paused by operator.")

    def resume(self, run_id: str, actor: str = "local.tester") -> None:
        controller = self._controllers.get(run_id)
        if controller is None:
            raise RunError(f"Run {run_id} is not active in this process. Use Resume Run to restart it.")
        controller.request_resume()
        self._record_control(run_id, ACTION_RUN_RESUMED, actor, "Run resumed by operator.")

    def cancel(self, run_id: str, actor: str = "local.tester") -> None:
        controller = self._controllers.get(run_id)
        if controller is None:
            raise RunError(f"Run {run_id} is not active in this process.")
        controller.request_cancel()
        self._record_control(run_id, ACTION_RUN_CANCELLED, actor, "Run cancelled by operator.")

    def is_active(self, run_id: str) -> bool:
        controller = self._controllers.get(run_id)
        return bool(controller and controller.thread and controller.thread.is_alive())

    def _record_control(self, run_id: str, action: str, actor: str, detail: str) -> None:
        with session_scope() as session:
            AuditLogger(session, run_id=run_id).record(
                action, actor=actor, actor_role="tester", entity_type="run", entity_ref=run_id, detail=detail
            )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _publish(self, run_id: str, payload: dict[str, Any]) -> None:
        self.hub.publish(run_id, payload)
        self.hub.publish("*", payload)

    def _execute(
        self,
        run_id: str,
        controller: RunController,
        actor: str,
        actor_role: str,
        resume: bool,
    ) -> None:
        started = time.monotonic()
        try:
            self._run_body(run_id, controller, actor, actor_role, resume, started)
        except Exception as exc:  # any escape is a run failure, never a silent stop
            log.exception("Run %s failed", run_id)
            with session_scope() as session:
                run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
                if run is not None:
                    run.status = STATUS_FAILED
                    run.message = str(exc)
                    run.finished_at = datetime.now(timezone.utc)
                    session.add(run)
                AuditLogger(session, run_id=run_id).record(
                    ACTION_RUN_FAILED,
                    actor=actor,
                    actor_role=actor_role,
                    entity_type="run",
                    entity_ref=run_id,
                    detail=str(exc),
                )
            self._publish(
                run_id,
                {
                    "run_id": run_id,
                    "status": STATUS_FAILED,
                    "stage": "failed",
                    "message": str(exc),
                    "elapsed_s": round(time.monotonic() - started, 2),
                },
            )

    def _run_body(
        self,
        run_id: str,
        controller: RunController,
        actor: str,
        actor_role: str,
        resume: bool,
        started: float,
    ) -> None:
        settings = get_settings()
        checkpoint = self.checkpoints.load(run_id) if resume else None
        if checkpoint is None:
            checkpoint = Checkpoint(run_id=run_id)

        # --- load the frozen run configuration ------------------------------
        with session_scope() as session:
            run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
            if run is None:
                raise RunError(f"Run {run_id} no longer exists.")
            request = RunRequest(**run.frozen_config["request"])
            connection_id = run.connection_profile_id or ""
            dry_run = run.dry_run
            run.status = STATUS_VALIDATING
            run.stage = PipelineStage.CONNECTION.value
            run.started_at = run.started_at or datetime.now(timezone.utc)
            session.add(run)
            AuditLogger(session, run_id=run_id).record(
                ACTION_RUN_STARTED,
                actor=actor,
                actor_role=actor_role,
                entity_type="run",
                entity_ref=run_id,
                detail=f"{'Resuming' if resume else 'Starting'} {'dry ' if dry_run else ''}run.",
            )

        controller.status = STATUS_VALIDATING
        self._publish(
            run_id,
            {"run_id": run_id, "status": STATUS_VALIDATING, "stage": PipelineStage.CONNECTION.value},
        )

        # --- connect ---------------------------------------------------------
        with session_scope() as session:
            manager = ConnectionManager(session)
            adapter = manager.adapter_for(connection_id)
            status = adapter.test_connection()
            if not status.connected:
                raise RunError(status.message)
            profile = manager.profile(connection_id)
            map_service_url = None
            if profile is not None:
                map_profile = manager.profile("map_service")
                if map_profile is not None and map_profile.enabled:
                    map_service_url = (map_profile.settings_json or {}).get("base_url")
            existing_keys = existing_event_keys(session)
            existing_reviews = reviews_for_keys(session)

        run_dir: Path | None = None
        evidence_generator = None
        if not dry_run:
            run_dir = new_run_directory(run_id)
            evidence_generator = EvidenceGenerator(run_dir)

        processor = EventProcessor(
            adapter=adapter,
            sensor_config=request.sensor_config,
            query=request.query,
            rule_overrides=request.rule_overrides,
            map_resolver=MapContextResolver(map_service_url),
            evidence_generator=evidence_generator,
            existing_reviews=existing_reviews,
            existing_keys=existing_keys,
        )

        # --- discover --------------------------------------------------------
        limit = request.limit or settings.max_events_per_run
        if dry_run:
            limit = min(limit, 10)

        estimate, _, _ = (None, False, "")
        try:
            estimate, _, _ = adapter.estimate_count(request.query)
        except AdapterError:
            estimate = None

        counters: dict[str, Any] = {
            "records_discovered": estimate or 0,
            "records_scanned": checkpoint.processed_count,
            "records_processed": checkpoint.processed_count,
            "records_matched_country": 0,
            "records_matched_scenario": 0,
            "candidate_issue_count": 0,
            "blocking_error_count": 0,
            "review_required_count": 0,
            "duplicates_merged": 0,
            "error_count": 0,
            "filtered_out": 0,
            **(checkpoint.counters or {}),
        }

        packages: list[ReviewPackage] = []
        processed_ids = set(checkpoint.processed_event_ids)
        cursor = checkpoint.page_cursor
        consecutive_errors = 0
        completed_stages: list[str] = list(checkpoint.completed_stages)
        final_status = STATUS_COMPLETED

        controller.status = STATUS_RUNNING
        with session_scope() as session:
            run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
            if run is not None:
                run.status = STATUS_RUNNING
                run.stage = PipelineStage.METADATA.value
                run.records_discovered = estimate or 0
                run.output_dir = str(run_dir) if run_dir else None
                session.add(run)

        # --- process ----------------------------------------------------------
        while True:
            if controller.cancel_flag.is_set():
                final_status = STATUS_CANCELLED
                break

            try:
                page = adapter.search_events(
                    request.query, cursor=cursor, limit=min(settings.page_size, max(1, limit - len(processed_ids)))
                )
            except AdapterError as exc:
                if exc.retryable:
                    counters["error_count"] += 1
                    self._save_checkpoint(run_id, checkpoint, cursor, processed_ids, counters, completed_stages)
                    raise RunError(
                        f"{exc.user_message} Events processed before the interruption: "
                        f"{counters['records_processed']}. Progress was checkpointed."
                    ) from exc
                raise RunError(exc.user_message) from exc

            if not page.event_ids:
                break

            for event_id in page.event_ids:
                if controller.cancel_flag.is_set():
                    final_status = STATUS_CANCELLED
                    break

                while controller.pause_flag.is_set() and not controller.cancel_flag.is_set():
                    controller.status = STATUS_PAUSED
                    self._publish(
                        run_id,
                        {
                            "run_id": run_id,
                            "status": STATUS_PAUSED,
                            "stage": "paused",
                            "records_processed": counters["records_processed"],
                        },
                    )
                    time.sleep(0.4)
                controller.status = STATUS_RUNNING

                if event_id in processed_ids:
                    continue
                if len(processed_ids) >= limit:
                    break

                counters["records_scanned"] += 1
                outcome = processor.process(event_id)
                processed_ids.add(event_id)

                if outcome.error:
                    counters["error_count"] += 1
                    consecutive_errors += 1
                    if consecutive_errors >= settings.consecutive_error_limit:
                        self._save_checkpoint(run_id, checkpoint, cursor, processed_ids, counters, completed_stages)
                        raise RunError(
                            f"{settings.consecutive_error_limit} consecutive source errors. "
                            f"Last error: {outcome.error}. Progress was checkpointed."
                        )
                    continue
                consecutive_errors = 0

                for stage in outcome.stages_completed:
                    if stage not in completed_stages:
                        completed_stages.append(stage)

                if outcome.filtered_out:
                    counters["filtered_out"] += 1
                    continue

                counters["records_matched_country"] += 1
                counters["records_matched_scenario"] += 1

                package = outcome.package
                if package is None:
                    continue

                counters["records_processed"] += 1
                counters["candidate_issue_count"] += len(package.validation.failures)
                counters["blocking_error_count"] += package.blocking_error_count
                if package.review_required:
                    counters["review_required_count"] += 1
                packages.append(package)

                if not dry_run:
                    with session_scope() as session:
                        _, duplicate = upsert_review_package(
                            session,
                            package,
                            run_pk=self._run_pk(session, run_id),
                            trajectory=outcome.context.trajectory if outcome.context else None,
                            bundle=outcome.context.bundle if outcome.context else None,
                        )
                        if duplicate:
                            counters["duplicates_merged"] += 1
                        AuditLogger(session, run_id=run_id, run_dir=run_dir).record(
                            ACTION_EVENT_BLOCKED
                            if package.status == RecordStatus.BLOCKED_DATA_ERROR
                            else ACTION_EVENT_PROCESSED,
                            actor=actor,
                            actor_role=actor_role,
                            entity_type="event",
                            entity_ref=package.anonymized_event_ref,
                            after={
                                "status": package.status.value,
                                "overall_confidence": package.overall_confidence,
                                "blocking_error_count": package.blocking_error_count,
                                "record_version": package.record_version,
                                "duplicate": duplicate,
                            },
                            detail=package.automation_recommendation,
                        )

                if counters["records_processed"] % max(1, settings.checkpoint_interval) == 0:
                    self._save_checkpoint(run_id, checkpoint, cursor, processed_ids, counters, completed_stages)

                self._publish_progress(run_id, controller, counters, completed_stages, started, package, estimate)

            if final_status == STATUS_CANCELLED or len(processed_ids) >= limit:
                break
            cursor = page.next_cursor
            if not cursor:
                break

        # --- finish -----------------------------------------------------------
        elapsed = time.monotonic() - started
        self._save_checkpoint(run_id, checkpoint, cursor, processed_ids, counters, completed_stages)

        outputs: dict[str, Any] = {}
        if final_status == STATUS_COMPLETED and not dry_run and run_dir is not None:
            outputs = self._write_outputs(
                run_id, run_dir, packages, request, counters, elapsed, actor, actor_role
            )
        elif dry_run:
            outputs = {
                "dry_run": True,
                "note": (
                    "Dry run complete. Nothing was written to the database, no evidence was produced "
                    "and no CSV was exported. "
                    f"{counters['records_processed']} event(s) were processed in memory."
                ),
            }

        with session_scope() as session:
            run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
            if run is not None:
                run.status = final_status
                run.stage = "cancelled" if final_status == STATUS_CANCELLED else PipelineStage.CSV.value
                run.finished_at = datetime.now(timezone.utc)
                run.completed_stages = completed_stages
                run.records_scanned = counters["records_scanned"]
                run.records_processed = counters["records_processed"]
                run.records_matched_country = counters["records_matched_country"]
                run.records_matched_scenario = counters["records_matched_scenario"]
                run.candidate_issue_count = counters["candidate_issue_count"]
                run.blocking_error_count = counters["blocking_error_count"]
                run.review_required_count = counters["review_required_count"]
                run.duplicates_merged = counters["duplicates_merged"]
                run.error_count = counters["error_count"]
                run.csv_rows_created = int(outputs.get("rows_written", 0) or 0)
                run.checkpoint = checkpoint.to_dict()
                run.message = outputs.get("note") or outputs.get("summary_message")
                session.add(run)

            if request.profile_id and final_status == STATUS_COMPLETED and not dry_run:
                configuration = session.scalar(
                    select(ConfigurationProfile).where(ConfigurationProfile.profile_id == request.profile_id)
                )
                if configuration is not None:
                    configuration.executed_count = (configuration.executed_count or 0) + 1
                    session.add(configuration)

            AuditLogger(session, run_id=run_id, run_dir=run_dir).record(
                ACTION_RUN_CANCELLED if final_status == STATUS_CANCELLED else ACTION_RUN_COMPLETED,
                actor=actor,
                actor_role=actor_role,
                entity_type="run",
                entity_ref=run_id,
                after={**counters, "elapsed_s": round(elapsed, 3), "dry_run": dry_run},
                detail=f"Run finished with status {final_status}.",
            )

        controller.status = final_status
        if final_status == STATUS_COMPLETED:
            self.checkpoints.discard(run_id)

        self._publish(
            run_id,
            {
                "run_id": run_id,
                "status": final_status,
                "stage": "finished",
                "completed_stages": completed_stages,
                "elapsed_s": round(elapsed, 2),
                **{k: v for k, v in counters.items() if isinstance(v, (int, float))},
                "outputs": outputs,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _run_pk(session: Any, run_id: str) -> int | None:
        run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
        return run.run_pk if run else None

    def _save_checkpoint(
        self,
        run_id: str,
        checkpoint: Checkpoint,
        cursor: str | None,
        processed_ids: set[str],
        counters: dict[str, Any],
        completed_stages: list[str],
    ) -> None:
        checkpoint.page_cursor = cursor
        checkpoint.processed_event_ids = sorted(processed_ids)
        checkpoint.processed_count = counters["records_processed"]
        checkpoint.discovered_count = counters["records_discovered"]
        checkpoint.last_processed_event = checkpoint.processed_event_ids[-1] if checkpoint.processed_event_ids else None
        checkpoint.counters = dict(counters)
        checkpoint.completed_stages = list(completed_stages)
        self.checkpoints.save(checkpoint)

        with session_scope() as session:
            run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
            if run is not None:
                run.checkpoint = checkpoint.to_dict()
                run.records_processed = counters["records_processed"]
                session.add(run)
            AuditLogger(session, run_id=run_id).record(
                ACTION_RUN_CHECKPOINT,
                entity_type="run",
                entity_ref=run_id,
                after={"processed": counters["records_processed"], "cursor": cursor},
            )

    def _publish_progress(
        self,
        run_id: str,
        controller: RunController,
        counters: dict[str, Any],
        completed_stages: list[str],
        started: float,
        package: ReviewPackage,
        estimate: int | None,
    ) -> None:
        elapsed = time.monotonic() - started
        processed = max(1, counters["records_processed"])
        remaining = None
        if estimate:
            per_event = elapsed / processed
            remaining = max(0.0, (estimate - processed) * per_event)

        self._publish(
            run_id,
            {
                "run_id": run_id,
                "status": controller.status,
                "stage": PipelineStage.VALIDATION.value,
                "completed_stages": completed_stages,
                "stage_order": PIPELINE_STAGE_ORDER,
                "records_discovered": estimate or counters["records_discovered"],
                "records_scanned": counters["records_scanned"],
                "records_processed": counters["records_processed"],
                "candidate_issue_count": counters["candidate_issue_count"],
                "blocking_error_count": counters["blocking_error_count"],
                "review_required_count": counters["review_required_count"],
                "filtered_out": counters["filtered_out"],
                "error_count": counters["error_count"],
                "elapsed_s": round(elapsed, 2),
                "estimated_remaining_s": round(remaining, 2) if remaining is not None else None,
                "current_event_ref": package.anonymized_event_ref,
                "current_status": package.status.value,
            },
        )

    def _write_outputs(
        self,
        run_id: str,
        run_dir: Path,
        packages: list[ReviewPackage],
        request: RunRequest,
        counters: dict[str, Any],
        elapsed: float,
        actor: str,
        actor_role: str,
    ) -> dict[str, Any]:
        builder = CsvBuilder(request.csv_template_id)
        with session_scope() as session:
            reviews = reviews_for_keys(session, {p.canonical_event_key for p in packages})

        rows = [
            build_export_row(
                package,
                reviews.get(package.canonical_event_key, {}),
                evidence_folder=f"evidence/{package.anonymized_event_ref}",
                evidence_manifest="evidence_manifest.csv",
            )
            for package in packages
        ]

        run_config = {
            "run_id": run_id,
            "software_version": SOFTWARE_VERSION,
            "contract_version": CONTRACT_VERSION,
            "model_version": METHOD_VERSION,
            "rule_version": get_config_store().rule_version_signature(),
            "request": request.model_dump(mode="json"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "production_submission_enabled": get_settings().allow_production_submission,
            "source_access_mode": get_settings().source_access_mode,
        }

        outputs = write_outputs(
            run_dir=run_dir,
            run_id=run_id,
            packages=packages,
            rows=rows,
            builder=builder,
            run_config=run_config,
            counters=counters,
            elapsed_s=elapsed,
            expected_country_code=request.query.country_code,
        )

        readiness = outputs["readiness"]
        with session_scope() as session:
            AuditLogger(session, run_id=run_id, run_dir=run_dir).record(
                ACTION_EXPORT_GENERATED if readiness["ready"] else ACTION_EXPORT_BLOCKED,
                actor=actor,
                actor_role=actor_role,
                entity_type="export",
                entity_ref=run_id,
                after=readiness,
                detail=(
                    f"CSV export ready with {outputs['rows_written']} row(s)."
                    if readiness["ready"]
                    else (
                        f"CSV NOT READY: {readiness['blocking_errors']} blocking error(s). "
                        f"{outputs['rejected_records']} record(s) written to rejected_records.csv."
                    )
                ),
            )

        outputs["summary_message"] = (
            f"{outputs['rows_written']} row(s) exported, {outputs['rejected_records']} rejected."
        )
        return outputs

    # ------------------------------------------------------------------
    def resumable_runs(self) -> list[str]:
        return self.checkpoints.list_resumable()


_manager: RunManager | None = None


def get_run_manager() -> RunManager:
    global _manager
    if _manager is None:
        _manager = RunManager()
    return _manager
