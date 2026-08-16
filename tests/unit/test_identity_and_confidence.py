"""Canonical identity, pseudonymisation, confidence routing and redaction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.evidence.redaction import RedactionPolicy
from backend.identity import (
    anonymized_event_ref,
    canonical_event_key,
    content_hash,
    pseudonymize,
)
from backend.models.contracts import ConfidenceBand
from backend.recommendations.confidence import (
    aggregate_overall,
    band_for,
    compute_field_confidence,
    is_safety_critical,
    may_auto_select,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestCanonicalIdentity:
    def test_the_same_event_always_produces_the_same_key(self):
        first = canonical_event_key("SES-A", "junction_interaction", NOW, "J-1")
        second = canonical_event_key("SES-A", "junction_interaction", NOW, "J-1")
        assert first == second
        assert len(first) == 64

    def test_a_different_junction_produces_a_different_key(self):
        assert canonical_event_key("SES-A", "e", NOW, "J-1") != canonical_event_key("SES-A", "e", NOW, "J-2")

    def test_sub_second_jitter_does_not_create_a_duplicate_record(self):
        # Two exports of the same event that disagree by 300 ms must collapse.
        jittered = NOW + timedelta(milliseconds=300)
        assert canonical_event_key("SES-A", "e", NOW, "J-1") == canonical_event_key("SES-A", "e", jittered, "J-1")

    def test_a_second_apart_is_treated_as_a_different_occurrence(self):
        later = NOW + timedelta(seconds=5)
        assert canonical_event_key("SES-A", "e", NOW, "J-1") != canonical_event_key("SES-A", "e", later, "J-1")

    def test_event_type_is_case_insensitive(self):
        assert canonical_event_key("S", "Junction", NOW, "J") == canonical_event_key("S", "junction", NOW, "J")

    def test_a_missing_event_time_still_yields_a_stable_key(self):
        assert canonical_event_key("S", "e", None, "J") == canonical_event_key("S", "e", None, "J")


class TestPseudonymisation:
    def test_the_same_input_maps_to_the_same_reference(self):
        assert anonymized_event_ref("EVT-0001") == anonymized_event_ref("EVT-0001")

    def test_different_inputs_map_to_different_references(self):
        assert anonymized_event_ref("EVT-0001") != anonymized_event_ref("EVT-0002")

    def test_the_source_identifier_never_appears_in_the_reference(self):
        reference = anonymized_event_ref("SESSION-SECRET-12345")
        assert "SECRET" not in reference
        assert "12345" not in reference

    def test_the_prefix_separates_namespaces(self):
        assert pseudonymize("x", prefix="SES") != pseudonymize("x", prefix="EVT")

    def test_content_hash_is_verifiable_and_unsalted(self):
        digest = content_hash(b"evidence")
        assert digest.startswith("sha256:")
        assert digest == content_hash(b"evidence")


class TestConfidenceRouting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.99, ConfidenceBand.AUTO_CONFIRM),
            (0.95, ConfidenceBand.AUTO_CONFIRM),
            (0.90, ConfidenceBand.VERIFY),
            (0.80, ConfidenceBand.VERIFY),
            (0.65, ConfidenceBand.SUGGEST),
            (0.50, ConfidenceBand.SUGGEST),
            (0.20, ConfidenceBand.MANUAL),
            (0.0, ConfidenceBand.MANUAL),
        ],
    )
    def test_bands_follow_the_configured_policy(self, value: float, expected: ConfidenceBand):
        assert band_for(value) == expected

    def test_values_below_the_hard_floor_are_never_auto_selected(self):
        assert not may_auto_select(0.49)
        assert not may_auto_select(0.0)

    def test_high_confidence_values_may_be_auto_selected(self):
        assert may_auto_select(0.97)

    def test_suggest_band_never_auto_selects(self):
        assert not may_auto_select(0.65)

    def test_traffic_light_state_is_safety_critical(self):
        assert is_safety_critical("traffic_light_state_confidence")
        assert not is_safety_critical("polygon_confidence")


class TestConfidenceComputation:
    def test_missing_evidence_does_not_silently_count_as_agreement(self):
        only_model, _ = compute_field_confidence("f", {"model_confidence": 0.9})
        corroborated, _ = compute_field_confidence(
            "f",
            {
                "model_confidence": 0.9,
                "cross_camera_agreement": 0.9,
                "map_agreement": 0.9,
                "temporal_stability": 0.9,
                "sensor_quality": 0.9,
            },
        )
        # Both are 0.9 because every present component agrees, but the
        # explanation must record which evidence was absent.
        assert only_model == pytest.approx(0.9)
        assert corroborated == pytest.approx(0.9)

    def test_the_explanation_names_the_missing_components(self):
        _, explanation = compute_field_confidence("f", {"model_confidence": 0.9})
        assert "map_agreement" in explanation.missing_components
        assert "renormalised" in explanation.narrative

    def test_weights_are_renormalised_over_the_available_components(self):
        _, explanation = compute_field_confidence(
            "f", {"model_confidence": 0.8, "map_agreement": 0.4}
        )
        assert sum(explanation.weights.values()) == pytest.approx(1.0, abs=1e-3)

    def test_no_evidence_yields_zero_and_a_stated_reason(self):
        value, explanation = compute_field_confidence("f", {"model_confidence": None})
        assert value == 0.0
        assert "No confidence evidence" in explanation.narrative

    def test_a_weak_component_pulls_the_result_down(self):
        strong, _ = compute_field_confidence("f", {"model_confidence": 0.95, "map_agreement": 0.95})
        mixed, _ = compute_field_confidence("f", {"model_confidence": 0.95, "map_agreement": 0.20})
        assert mixed < strong


class TestOverallAggregation:
    def test_a_single_weak_field_cannot_be_averaged_away(self):
        weak = aggregate_overall({"a": 0.99, "b": 0.99, "c": 0.99, "d": 0.20})
        mean = (0.99 * 3 + 0.20) / 4
        assert weak < mean

    def test_all_strong_fields_score_high(self):
        assert aggregate_overall({"a": 0.98, "b": 0.97}) > 0.95

    def test_no_fields_means_no_confidence(self):
        assert aggregate_overall({}) == 0.0


class TestRedaction:
    @pytest.fixture()
    def policy(self) -> RedactionPolicy:
        return RedactionPolicy()

    def test_credentials_are_masked(self, policy: RedactionPolicy):
        redacted, report = policy.redact_mapping({"note": "authorization: Bearer abcdef0123456789ABCDEF"})
        assert "abcdef0123456789" not in str(redacted)
        assert any(hit.pattern_name == "bearer_token" for hit in report.hits)

    def test_email_addresses_are_masked(self, policy: RedactionPolicy):
        redacted, report = policy.redact_mapping({"owner": "tester@example.com"})
        assert "tester@example.com" not in str(redacted)
        assert report.hits

    def test_precise_coordinates_are_reduced(self, policy: RedactionPolicy):
        redacted, report = policy.redact_mapping({"latitude": 48.13743211, "longitude": 11.57549123})
        assert redacted["latitude"] == pytest.approx(48.14, abs=0.005)
        assert "latitude" in report.rounded_fields

    def test_identifiers_are_pseudonymised_not_removed(self, policy: RedactionPolicy):
        redacted, report = policy.redact_mapping({"session_id": "SESS-042"})
        assert redacted["session_id"] != "SESS-042"
        assert redacted["session_id"].startswith("SES-")
        assert "session_id" in report.pseudonymised_fields

    def test_nested_structures_are_redacted(self, policy: RedactionPolicy):
        redacted, _ = policy.redact_mapping({"outer": {"inner": ["contact: a@b.com"]}})
        assert "a@b.com" not in str(redacted)

    def test_preview_reports_without_writing(self, policy: RedactionPolicy):
        preview = policy.preview({"session_id": "S-1", "note": "user@example.com"})
        assert preview["enabled"]
        assert preview["report"]["pseudonymised_fields"]
        assert "user@example.com" not in str(preview["redacted_sample"])
