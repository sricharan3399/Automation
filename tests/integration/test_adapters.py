"""Adapter behaviour: local files, the Data Scout stub, and demo-mode refusal.

The central assertion of this module is negative: an unconfigured or
unavailable source must fail loudly and never fabricate results.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.connectors.base import (
    AdapterAuthError,
    AdapterNotConfigured,
    AdapterPermissionError,
    AdapterSchemaError,
    AdapterUnavailable,
    DemoDataRefused,
)
from backend.connectors.data_scout import NvidiaInternalDataScoutAdapter
from backend.connectors.local_files import LocalFilesAdapter
from backend.connectors.normalization import (
    build_event_metadata,
    is_authoritative_country_source,
    normalize_value,
    suggest_mapping,
)
from backend.connectors.synthetic import SyntheticAdapter
from backend.models.contracts import ScoutQuery
from backend.settings import reset_settings_cache


class TestLocalFilesAdapter:
    def test_it_connects_and_reports_the_event_count(self, local_adapter: LocalFilesAdapter):
        status = local_adapter.test_connection()
        assert status.connected
        assert status.status == "CONNECTED"
        assert "events available" in status.message

    def test_a_missing_directory_is_reported_not_faked(self, tmp_path):
        adapter = LocalFilesAdapter({"dataset_dir": str(tmp_path / "nope")})
        status = adapter.test_connection()
        assert not status.connected
        assert status.status == "NOT_CONFIGURED"
        with pytest.raises(AdapterNotConfigured):
            adapter.authenticate()

    def test_the_vocabulary_comes_from_the_data(self, local_adapter: LocalFilesAdapter):
        filters = local_adapter.get_supported_filters()
        assert filters.origin == "source"
        assert "DE" in filters.values["country_code"]
        assert "bus" in filters.values["object_type"]

    def test_dependent_filters_narrow_to_the_current_query(self, local_adapter: LocalFilesAdapter):
        everything = local_adapter.get_supported_filters()
        german_only = local_adapter.get_supported_filters(ScoutQuery(country_code="DE"))
        assert set(german_only.values["city"]) <= set(everything.values["city"])
        assert german_only.values["country_code"] == ["DE"]

    def test_country_filtering_is_exact(self, local_adapter: LocalFilesAdapter):
        german = local_adapter.search_events(ScoutQuery(country_code="DE"), limit=500)
        french = local_adapter.search_events(ScoutQuery(country_code="FR"), limit=500)
        assert german.event_ids
        assert french.event_ids
        assert not set(german.event_ids) & set(french.event_ids)

    def test_the_count_estimate_is_exact_for_a_local_dataset(self, local_adapter: LocalFilesAdapter):
        count, exact, _ = local_adapter.estimate_count(ScoutQuery(country_code="DE"))
        page = local_adapter.search_events(ScoutQuery(country_code="DE"), limit=500)
        assert exact
        assert count == len(page.event_ids)

    def test_pagination_covers_every_event_exactly_once(self, local_adapter: LocalFilesAdapter):
        collected: list[str] = []
        cursor = None
        while True:
            page = local_adapter.search_events(ScoutQuery(), cursor=cursor, limit=5)
            collected.extend(page.event_ids)
            cursor = page.next_cursor
            if not cursor:
                break
        assert len(collected) == len(set(collected))
        assert len(collected) == local_adapter.estimate_count(ScoutQuery())[0]

    def test_a_full_bundle_is_assembled(self, local_adapter: LocalFilesAdapter):
        event_id = local_adapter.search_events(ScoutQuery(country_code="DE"), limit=1).event_ids[0]
        bundle = local_adapter.get_event_bundle(event_id)
        assert bundle.metadata.country_code == "DE"
        assert bundle.streams
        assert bundle.poses
        assert bundle.source_end_t and bundle.source_end_t > 0

    def test_an_unknown_event_raises_rather_than_returning_empty(self, local_adapter: LocalFilesAdapter):
        with pytest.raises(AdapterSchemaError):
            local_adapter.get_event_metadata("NOPE-9999")

    def test_a_dataset_manifest_is_not_mistaken_for_an_event(self, local_adapter: LocalFilesAdapter):
        assert "manifest" not in " ".join(local_adapter.search_events(ScoutQuery(), limit=500).event_ids).lower()


class TestSchemaDiscovery:
    def test_source_field_names_are_mapped_onto_canonical_names(self, local_adapter: LocalFilesAdapter):
        schema = local_adapter.get_schema()
        mapped = {f.source_field: f.canonical_field for f in schema.fields if f.canonical_field}
        assert mapped.get("country_code") == "country_code"
        assert mapped.get("road_class") == "road_type"
        assert mapped.get("num_lanes") == "lane_count"
        assert mapped.get("junction_type") == "intersection_type"

    def test_mapping_confidence_and_method_are_reported(self, local_adapter: LocalFilesAdapter):
        schema = local_adapter.get_schema()
        exact = [f for f in schema.fields if f.mapping_method == "exact"]
        assert exact and all(f.mapping_confidence == 1.0 for f in exact)

    def test_unrecognised_fields_are_reported_not_dropped(self):
        schema = suggest_mapping([{"event_id": "E1", "some_unknown_vendor_field": 42}])
        unknown = next(f for f in schema.fields if f.source_field == "some_unknown_vendor_field")
        assert unknown.canonical_field is None
        assert unknown.mapping_method == "unmapped"

    def test_vocabulary_is_normalised_onto_canonical_values(self):
        assert normalize_value("road_type", "Innerstadt") == "urban"
        assert normalize_value("weather", "Rainy") == "rain"
        assert normalize_value("object_type", ["Omnibus"]) == ["bus"]

    def test_an_unmapped_value_is_preserved_rather_than_discarded(self):
        assert normalize_value("road_type", "vendor_specific_class") == "vendor_specific_class"

    def test_a_filename_is_never_an_authoritative_country_source(self):
        assert not is_authoritative_country_source("source_filename")
        assert not is_authoritative_country_source("blob_path")
        assert is_authoritative_country_source("country_code")

    def test_country_resolution_records_where_the_value_came_from(self):
        metadata = build_event_metadata({"event": "E", "country_code": "DE", "country_name": "Germany"})
        assert metadata.country_code == "DE"
        assert metadata.country_source_field == "country_code"

    def test_a_country_name_alone_still_resolves_to_a_code(self):
        metadata = build_event_metadata({"event": "E", "country_name": "France"})
        assert metadata.country_code == "FR"


class TestDataScoutAdapter:
    def test_it_is_not_configured_out_of_the_box(self):
        adapter = NvidiaInternalDataScoutAdapter({})
        assert not adapter.is_configured
        status = adapter.test_connection()
        assert not status.connected
        assert status.status == "NOT_CONFIGURED"

    def test_the_message_states_exactly_what_is_missing(self):
        adapter = NvidiaInternalDataScoutAdapter({})
        message = adapter.test_connection().message
        assert "NOT CONFIGURED" in message
        assert "Base URL" in message
        assert "will not simulate" in message

    def test_every_data_method_refuses_while_unconfigured(self):
        adapter = NvidiaInternalDataScoutAdapter({})
        for call in (
            lambda: adapter.search_events(ScoutQuery()),
            lambda: adapter.get_event_metadata("E1"),
            lambda: adapter.get_projects(),
            lambda: adapter.get_datasets(),
            lambda: adapter.get_sensor_manifest("E1"),
            lambda: adapter.get_map_context("E1"),
        ):
            with pytest.raises(AdapterNotConfigured):
                call()

    def test_missing_configuration_is_enumerated(self):
        missing = NvidiaInternalDataScoutAdapter({}).missing_configuration()
        assert "base_url" in missing
        assert "endpoints.search_events" in missing

    def test_a_non_rest_integration_requires_a_dedicated_adapter(self):
        adapter = NvidiaInternalDataScoutAdapter(
            {
                "enabled": True,
                "base_url": "https://example.internal",
                "integration_type": "graphql",
                "endpoints": {"search_events": "/s", "event_metadata": "/e/{event_id}"},
            }
        )
        assert any("dedicated adapter" in item for item in adapter.missing_configuration())

    def test_filters_the_source_cannot_express_are_reported_not_dropped(self):
        adapter = NvidiaInternalDataScoutAdapter({"query_translation": {"country_code": "cc"}})
        untranslated = adapter.untranslated_filters(
            ScoutQuery(country_code="DE", object_types=["bus"], weather=["rain"])
        )
        assert "object_type" in untranslated
        assert "weather" in untranslated
        assert "country_code" not in untranslated

    def test_query_translation_uses_the_configured_parameter_names(self):
        adapter = NvidiaInternalDataScoutAdapter(
            {"query_translation": {"country_code": "cc", "object_type": "classes"}}
        )
        native = adapter.translate_query(ScoutQuery(country_code="DE", object_types=["bus", "truck"]))
        assert native == {"cc": "DE", "classes": "bus,truck"}

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            (401, AdapterAuthError),
            (403, AdapterPermissionError),
            (404, AdapterSchemaError),
            (429, AdapterUnavailable),
            (500, AdapterUnavailable),
            (503, AdapterUnavailable),
        ],
    )
    def test_http_failures_become_actionable_errors(self, status_code: int, expected: type[Exception]):
        response = httpx.Response(status_code, request=httpx.Request("GET", "https://x/y"))
        with pytest.raises(expected) as raised:
            NvidiaInternalDataScoutAdapter._handle_response(response, "/y")
        assert raised.value.user_message
        assert str(status_code) not in raised.value.user_message or "HTTP" in raised.value.user_message

    def test_a_non_json_response_is_reported_clearly(self):
        response = httpx.Response(200, text="<html>not json</html>", request=httpx.Request("GET", "https://x/y"))
        with pytest.raises(AdapterSchemaError):
            NvidiaInternalDataScoutAdapter._handle_response(response, "/y")


class TestSyntheticAdapterGuard:
    def test_production_mode_refuses_synthetic_data(self, monkeypatch):
        monkeypatch.setenv("AV_MODE", "production")
        reset_settings_cache()
        adapter = SyntheticAdapter({})
        with pytest.raises(DemoDataRefused):
            adapter.authenticate()
        status = adapter.test_connection()
        assert not status.connected
        assert status.status == "DEMO_ONLY"
        assert "refused" in status.message

    def test_demo_mode_allows_it_and_labels_every_record(self, monkeypatch):
        monkeypatch.setenv("AV_MODE", "demo")
        reset_settings_cache()
        adapter = SyntheticAdapter({})
        adapter.authenticate()
        status = adapter.test_connection()
        assert status.connected
        assert "DEMO MODE" in status.message

        event_id = adapter.search_events(ScoutQuery(), limit=1).event_ids[0]
        assert adapter.get_event_bundle(event_id).is_synthetic


class TestGoldenDataset:
    def test_every_fixture_is_labelled_synthetic(self, golden_documents):
        assert golden_documents
        assert all(document.get("is_synthetic") for document in golden_documents)

    def test_the_generator_is_deterministic(self, golden_documents):
        """The committed fixtures must be reproducible from source.

        Compared by digest, and reported per event: a full-text diff of two
        multi-megabyte documents would take longer to render than the whole
        suite takes to run.
        """
        import hashlib

        from backend.connectors.synthetic import generate_documents

        def digest(document: dict) -> str:
            return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()

        regenerated = generate_documents()
        assert len(regenerated) == len(golden_documents)

        mismatched = [
            committed["metadata"]["event"]
            for committed, fresh in zip(regenerated, golden_documents, strict=True)
            if digest(committed) != digest(fresh)
        ]
        assert not mismatched, (
            f"{len(mismatched)} golden fixture(s) differ from a fresh generation: "
            f"{mismatched[:5]}. Re-run tests/golden_dataset/generate.py, or fix the "
            "non-determinism in backend/connectors/synthetic.py."
        )

    def test_the_dataset_covers_every_blocking_fault(self, golden_documents):
        injected = {fault for document in golden_documents for fault in document.get("injected_faults", [])}
        for required in (
            "missing_vehicle_state",
            "reversed_eval_window",
            "self_intersecting_polygon",
            "two_point_polygon",
            "missing_country_code",
        ):
            assert required in injected, f"golden dataset is missing the '{required}' case"

    def test_the_dataset_covers_difficult_and_easy_cases(self, golden_documents):
        difficulties = {document.get("difficulty") for document in golden_documents}
        assert {"easy", "moderate", "hard", "blocking"} <= difficulties
