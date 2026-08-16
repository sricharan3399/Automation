"""End-to-end pipeline behaviour and the HTTP API."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.connectors.local_files import LocalFilesAdapter
from backend.database.init_db import initialize_database
from backend.models.contracts import (
    RecordStatus,
    RunRequest,
    ScoutQuery,
    SensorConfiguration,
    StreamRequirement,
    StreamRequirementSpec,
)
from backend.pipeline.orchestrator import EventProcessor
from backend.settings import PROJECT_ROOT
from backend.version import API_PREFIX

GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden_dataset"


@pytest.fixture()
def processor() -> EventProcessor:
    adapter = LocalFilesAdapter({"dataset_dir": str(GOLDEN_DIR)})
    adapter.authenticate()
    return EventProcessor(
        adapter=adapter,
        sensor_config=SensorConfiguration(
            streams=[
                StreamRequirementSpec(stream_type="vehicle_state", requirement=StreamRequirement.REQUIRED)
            ]
        ),
        query=ScoutQuery(country_code="DE", object_types=["bus"]),
    )


def event_id_with_fault(golden_documents, fault: str) -> str:
    for document in golden_documents:
        if fault in (document.get("injected_faults") or []):
            return str(document["metadata"]["event"])
    raise AssertionError(f"no golden event with fault '{fault}'")


def clean_event_id(golden_documents) -> str:
    for document in golden_documents:
        metadata = document["metadata"]
        if not document.get("injected_faults") and metadata.get("country_code") == "DE":
            return str(metadata["event"])
    raise AssertionError("no clean German golden event")


class TestPipeline:
    def test_a_clean_event_produces_a_complete_review_package(self, processor, golden_documents):
        outcome = processor.process(clean_event_id(golden_documents))
        assert outcome.ok
        package = outcome.package
        assert package is not None
        assert package.canonical_event_key
        assert package.anonymized_event_ref.startswith("EVT-")
        assert package.recommendations
        assert package.validation.outcomes
        assert package.geometry.available
        assert package.geometry.target_junction is not None
        assert package.scene.available
        assert package.behavior.available

    def test_the_source_event_id_never_leaks_into_the_anonymised_reference(self, processor, golden_documents):
        source_id = clean_event_id(golden_documents)
        package = processor.process(source_id).package
        assert package is not None
        assert source_id not in package.anonymized_event_ref
        assert source_id not in package.anonymized_session_ref

    def test_processing_is_deterministic(self, processor, golden_documents):
        event_id = clean_event_id(golden_documents)
        first = processor.process(event_id).package
        second = processor.process(event_id).package
        assert first is not None and second is not None
        assert first.canonical_event_key == second.canonical_event_key
        assert first.overall_confidence == second.overall_confidence

    def test_a_non_matching_event_is_filtered_with_a_reason(self, processor, golden_documents):
        french = next(
            str(d["metadata"]["event"])
            for d in golden_documents
            if d["metadata"].get("country_code") == "FR"
        )
        outcome = processor.process(french)
        assert outcome.filtered_out
        assert outcome.filter_reason and "country_code" in outcome.filter_reason
        assert outcome.package is None

    def test_a_missing_required_stream_blocks_before_analysis(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "missing_vehicle_state"))
        assert outcome.package is not None
        assert outcome.package.status == RecordStatus.BLOCKED_DATA_ERROR
        # Analysis must not run on data already known to be unusable.
        assert not outcome.package.geometry.available
        assert "not a vehicle finding" in outcome.package.automation_recommendation

    def test_a_reversed_evaluation_window_fails_its_rule(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "reversed_eval_window"))
        assert outcome.package is not None
        failed = {o.rule_id for o in outcome.package.validation.failures}
        assert "TEMPORAL_EVAL_WINDOW_ORDER" in failed

    def test_a_self_intersecting_polygon_fails_its_rule(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "self_intersecting_polygon"))
        assert outcome.package is not None
        failed = {o.rule_id for o in outcome.package.validation.failures}
        assert "GEOMETRY_POLYGON_VALID" in failed

    def test_an_event_without_a_country_code_is_filtered_out(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "missing_country_code"))
        assert outcome.filtered_out

    def test_a_track_identity_switch_is_reported_as_a_candidate(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "track_id_switch"))
        assert outcome.package is not None
        codes = {f.code for f in outcome.package.scene.tracking_findings}
        assert "BUS_TRACK_ID_SWITCH" in codes
        assert all(f.status == RecordStatus.CANDIDATE for f in outcome.package.scene.tracking_findings)

    def test_a_missed_detection_is_reported_when_reference_data_exists(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "missed_detection"))
        assert outcome.package is not None
        assert "BUS_MISSED_DETECTION" in {f.code for f in outcome.package.scene.perception_findings}

    def test_without_reference_data_perception_rules_are_skipped_not_passed(self, processor, golden_documents):
        no_reference = next(
            str(d["metadata"]["event"])
            for d in golden_documents
            if not d.get("reference_data_available") and d["metadata"].get("country_code") == "DE"
        )
        outcome = processor.process(no_reference)
        assert outcome.package is not None
        missed = next(o for o in outcome.package.validation.outcomes if o.rule_id == "BUS_MISSED_DETECTION")
        assert missed.skipped
        assert "reference" in (missed.skip_reason or "").lower()

    def test_map_misalignment_is_detected(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "map_misalignment"))
        assert outcome.package is not None
        assert "GEOMETRY_MAP_ALIGNMENT" in {o.rule_id for o in outcome.package.validation.failures}

    def test_an_illegal_signal_transition_is_detected(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "illegal_signal_transition"))
        assert outcome.package is not None
        assert "TRAFFIC_LIGHT_STATE_CONSISTENCY" in {o.rule_id for o in outcome.package.validation.failures}

    def test_safety_critical_disagreement_escalates_to_senior_review(self, processor, golden_documents):
        outcome = processor.process(event_id_with_fault(golden_documents, "signal_metadata_mismatch"))
        assert outcome.package is not None
        assert outcome.package.status == RecordStatus.SENIOR_REVIEW_REQUIRED
        assert "Senior review required" in outcome.package.automation_recommendation

    def test_behaviour_reports_observations_and_never_an_interpretation(self, processor, golden_documents):
        outcome = processor.process(clean_event_id(golden_documents))
        assert outcome.package is not None
        observations = outcome.package.behavior.observations
        assert observations
        assert all(o.interpretation is None for o in observations)

    def test_low_confidence_fields_are_left_blank_rather_than_guessed(self, processor, golden_documents):
        outcome = processor.process(clean_event_id(golden_documents))
        assert outcome.package is not None
        for recommendation in outcome.package.recommendations:
            if recommendation.confidence < 0.5:
                assert not recommendation.auto_selected
                assert recommendation.recommended_value is None

    def test_every_rule_is_either_evaluated_or_skipped_with_a_reason(self, processor, golden_documents):
        outcome = processor.process(clean_event_id(golden_documents))
        assert outcome.package is not None
        for entry in outcome.package.validation.outcomes:
            if entry.skipped:
                assert entry.skip_reason, f"{entry.rule_id} was skipped with no reason"

    def test_rules_awaiting_a_project_threshold_are_skipped_not_passed(self, processor, golden_documents):
        outcome = processor.process(clean_event_id(golden_documents))
        assert outcome.package is not None
        entry = next(
            (o for o in outcome.package.validation.outcomes if o.rule_id == "BUS_DISTANCE_ESTIMATION_ERROR"),
            None,
        )
        if entry is not None:
            assert entry.skipped
            assert "approved project threshold" in (entry.skip_reason or "")


class TestApi:
    @pytest.fixture()
    def client(self):
        initialize_database()
        from backend.main import create_app

        with TestClient(create_app()) as test_client:
            yield test_client

    def test_health_reports_the_safety_posture(self, client):
        payload = client.get(f"{API_PREFIX}/health").json()
        assert payload["status"] == "ok"
        assert payload["source_access_mode"] == "read_only"
        assert payload["production_submission_enabled"] is False

    def test_the_home_dashboard_aggregates_in_one_call(self, client):
        payload = client.get(f"{API_PREFIX}/home").json()
        assert payload["connections"]
        assert payload["quick_actions"]
        assert "gpu" in payload

    def test_the_filter_vocabulary_labels_its_origin(self, client):
        payload = client.get(f"{API_PREFIX}/taxonomy/filters").json()
        assert payload["fields"]
        assert all("origin" in field for field in payload["fields"].values())

    def test_built_in_profiles_are_seeded(self, client):
        profiles = client.get(f"{API_PREFIX}/profiles").json()["profiles"]
        ids = {p["profile_id"] for p in profiles}
        assert "germany_bus_validation" in ids
        assert all(p["is_builtin"] for p in profiles if p["profile_id"] == "germany_bus_validation")

    def test_a_bundled_profile_cannot_be_overwritten(self, client):
        response = client.put(
            f"{API_PREFIX}/profiles/germany_bus_validation",
            json={"profile_id": "germany_bus_validation", "name": "hijacked"},
        )
        assert response.status_code == 409

    def test_a_profile_containing_a_secret_is_refused(self, client):
        response = client.put(
            f"{API_PREFIX}/profiles/leaky",
            json={
                "profile_id": "leaky",
                "name": "leaky",
                "evidence_config": {"api_key": "super-secret"},
            },
        )
        assert response.status_code == 400
        assert "never store credentials" in str(response.json()["detail"])

    def test_a_connection_update_carrying_a_secret_is_refused(self, client):
        response = client.put(
            f"{API_PREFIX}/connections/nvidia_data_scout",
            json={"settings": {"token": "abc123"}},
        )
        assert response.status_code == 400
        assert "never stored" in str(response.json()["detail"])

    def test_the_data_scout_connection_reports_itself_unconfigured(self, client):
        payload = client.post(f"{API_PREFIX}/connections/nvidia_data_scout/test").json()
        assert payload["connected"] is False
        assert payload["status"] == "NOT_CONFIGURED"

    def test_the_rule_catalogue_marks_rules_awaiting_a_threshold(self, client):
        payload = client.get(f"{API_PREFIX}/rules").json()
        awaiting = [r for r in payload["rules"] if r["awaiting_project_threshold"]]
        assert awaiting
        assert all(r["state"] == "AWAITING APPROVED PROJECT THRESHOLD" for r in awaiting)
        assert all(not r["enabled"] for r in awaiting)

    def test_production_submission_is_refused_with_the_approved_flow_stated(self, client):
        response = client.post(f"{API_PREFIX}/admin/production-submission")
        assert response.status_code == 501
        detail = response.json()["detail"]
        assert detail["allow_production_submission"] is False
        assert "read-back verification" in detail["message"]

    def test_the_audit_api_exposes_no_mutation(self, client):
        assert client.post(f"{API_PREFIX}/audit").status_code in (404, 405)
        assert client.delete(f"{API_PREFIX}/audit/1").status_code in (404, 405)

    def test_a_dry_run_writes_nothing(self, client):
        request = RunRequest(
            query=ScoutQuery(country_code="DE", object_types=["bus"]),
            connection_id="local_files",
            dry_run=True,
            limit=3,
        )
        created = client.post(f"{API_PREFIX}/runs", json=request.model_dump(mode="json"))
        assert created.status_code == 201
        run_id = created.json()["run_id"]

        for _ in range(120):
            payload = client.get(f"{API_PREFIX}/runs/{run_id}").json()
            if payload["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.25)

        assert payload["status"] == "COMPLETED"
        assert payload["dry_run"] is True
        assert payload["counters"]["records_processed"] > 0
        # Nothing persisted, nothing exported.
        assert client.get(f"{API_PREFIX}/events").json()["total"] == 0
        assert payload["counters"]["csv_rows_created"] == 0

    def test_a_full_run_persists_events_and_produces_outputs(self, client):
        request = RunRequest(
            query=ScoutQuery(country_code="DE", object_types=["bus"]),
            connection_id="local_files",
            dry_run=False,
            limit=6,
        )
        run_id = client.post(f"{API_PREFIX}/runs", json=request.model_dump(mode="json")).json()["run_id"]

        for _ in range(240):
            payload = client.get(f"{API_PREFIX}/runs/{run_id}").json()
            if payload["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.25)

        assert payload["status"] == "COMPLETED", payload.get("message")
        assert payload["counters"]["records_processed"] > 0

        events = client.get(f"{API_PREFIX}/events").json()
        assert events["total"] == payload["counters"]["records_processed"]

        # Re-running the same events must upsert, never duplicate.
        second = client.post(f"{API_PREFIX}/runs", json=request.model_dump(mode="json")).json()["run_id"]
        for _ in range(240):
            payload2 = client.get(f"{API_PREFIX}/runs/{second}").json()
            if payload2["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.25)
        assert payload2["counters"]["duplicates_merged"] == payload2["counters"]["records_processed"]
        assert client.get(f"{API_PREFIX}/events").json()["total"] == events["total"]

    def test_review_decisions_are_recorded_and_audited(self, client):
        request = RunRequest(
            query=ScoutQuery(country_code="DE", object_types=["bus"]),
            connection_id="local_files",
            dry_run=False,
            limit=2,
        )
        run_id = client.post(f"{API_PREFIX}/runs", json=request.model_dump(mode="json")).json()["run_id"]
        for _ in range(240):
            if client.get(f"{API_PREFIX}/runs/{run_id}").json()["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.25)

        events = client.get(f"{API_PREFIX}/events").json()["events"]
        assert events
        key = events[0]["canonical_event_key"]

        detail = client.get(f"{API_PREFIX}/review/{key}").json()
        assert detail["fields"]
        field = detail["fields"][0]["field"]

        response = client.post(
            f"{API_PREFIX}/review/{key}/decisions",
            json={
                "canonical_event_key": key,
                "decisions": [{"field_name": field, "decision": "ACCEPT"}],
                "finalize": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["recorded"][0]["decision"] == "ACCEPT"

        audit = client.get(f"{API_PREFIX}/audit?limit=200").json()["entries"]
        assert any(entry["action"].startswith("review.") for entry in audit)

    def test_an_override_without_a_reason_is_refused(self, client):
        request = RunRequest(
            query=ScoutQuery(country_code="DE", object_types=["bus"]),
            connection_id="local_files",
            dry_run=False,
            limit=8,
        )
        run_id = client.post(f"{API_PREFIX}/runs", json=request.model_dump(mode="json")).json()["run_id"]
        for _ in range(240):
            if client.get(f"{API_PREFIX}/runs/{run_id}").json()["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.25)

        events = client.get(f"{API_PREFIX}/events").json()["events"]
        target = None
        for event in events:
            detail = client.get(f"{API_PREFIX}/review/{event['canonical_event_key']}").json()
            safety = [f for f in detail["fields"] if f["safety_critical"]]
            if safety:
                target = (event["canonical_event_key"], safety[0]["field"])
                break
        if target is None:
            pytest.skip("no safety-critical field present in this sample")

        key, field = target
        response = client.post(
            f"{API_PREFIX}/review/{key}/decisions",
            json={
                "canonical_event_key": key,
                "decisions": [{"field_name": field, "decision": "REJECT", "override_reason": "no"}],
                "finalize": False,
            },
        )
        assert response.status_code == 400
        assert "override reason" in str(response.json()["detail"]).lower()

    def test_export_preview_reports_readiness_without_writing(self, client):
        request = RunRequest(
            query=ScoutQuery(country_code="DE", object_types=["bus"]),
            connection_id="local_files",
            dry_run=False,
            limit=4,
        )
        run_id = client.post(f"{API_PREFIX}/runs", json=request.model_dump(mode="json")).json()["run_id"]
        for _ in range(240):
            if client.get(f"{API_PREFIX}/runs/{run_id}").json()["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.25)

        preview = client.post(
            f"{API_PREFIX}/reports/preview",
            json={"run_id": run_id, "template_id": "germany_bus_test", "preview_only": True},
        ).json()
        assert preview["total_rows"] > 0
        assert preview["readiness"]["ready"] is True, preview["readiness"]["issues"][:3]
        assert "canonical_event_key" in preview["headers"]
