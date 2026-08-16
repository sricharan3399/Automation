"""CSV validation and export, plus client-side query matching."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.configstore import get_config_store
from backend.connectors.matching import evaluate
from backend.models.contracts import EventMetadata, LaneFilter, ScoutQuery
from backend.reports.csv_builder import CsvBuilder
from backend.reports.csv_validation import format_cell, split_rows, validate_rows

NOW = datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc)


def metadata(**overrides) -> EventMetadata:
    base = {
        "event_id": "EVT-1",
        "session_id": "SESS-1",
        "country": "Germany",
        "country_code": "DE",
        "country_source_field": "country_code",
        "city": "Munich",
        "region": "Bavaria",
        "event_time": NOW,
        "road_type": "urban",
        "lane_count": 3,
        "intersection_type": "four_way_junction",
        "traffic_control_entity": ["traffic_light"],
        "weather": "clear",
        "lighting": "day",
        "object_type": ["bus"],
        "bus_type": "city_bus",
        "scenario_tags": ["BUS_AHEAD_SAME_LANE"],
    }
    base.update(overrides)
    return EventMetadata(**base)


def row(**overrides) -> dict:
    base = {
        "canonical_event_key": "k" * 64,
        "anonymized_event_ref": "EVT-ABC",
        "record_version": 1,
        "country": "Germany",
        "country_code": "DE",
        "object_type": ["bus"],
        "final_status": "REVIEW_REQUIRED",
        "submission_blocking_error_count": 0,
        "rule_version": "rules-1.0.0",
        "last_updated_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Query matching
# ---------------------------------------------------------------------------
class TestQueryMatching:
    def test_an_empty_query_matches_everything(self):
        matched, reason = evaluate(ScoutQuery(), metadata())
        assert matched and reason is None

    def test_country_is_matched_on_the_authoritative_code(self):
        assert evaluate(ScoutQuery(country_code="DE"), metadata())[0]
        assert not evaluate(ScoutQuery(country_code="FR"), metadata())[0]

    def test_an_event_without_a_country_code_cannot_satisfy_a_country_filter(self):
        matched, reason = evaluate(ScoutQuery(country_code="DE"), metadata(country_code=None))
        assert not matched
        assert "country_code" in (reason or "")

    def test_a_multi_select_matches_on_any_selected_value(self):
        assert evaluate(ScoutQuery(road_types=["rural", "urban"]), metadata())[0]
        assert not evaluate(ScoutQuery(road_types=["autobahn"]), metadata())[0]

    def test_object_type_matches_within_a_list(self):
        assert evaluate(ScoutQuery(object_types=["bus"]), metadata())[0]
        assert not evaluate(ScoutQuery(object_types=["tram"]), metadata())[0]

    def test_lane_count_filters_exactly_and_by_range(self):
        assert evaluate(ScoutQuery(lanes=LaneFilter(lane_count_any=False, lane_count_exact=[3])), metadata())[0]
        assert not evaluate(ScoutQuery(lanes=LaneFilter(lane_count_any=False, lane_count_exact=[1])), metadata())[0]
        assert evaluate(ScoutQuery(lanes=LaneFilter(min_lanes=2, max_lanes=4)), metadata())[0]
        assert not evaluate(ScoutQuery(lanes=LaneFilter(min_lanes=4)), metadata())[0]

    def test_five_plus_lanes_is_treated_as_a_minimum(self):
        query = ScoutQuery(lanes=LaneFilter(lane_count_any=False, lane_count_exact=[5]))
        assert evaluate(query, metadata(lane_count=7))[0]
        assert not evaluate(query, metadata(lane_count=3))[0]

    def test_a_date_range_excludes_events_outside_it(self):
        query = ScoutQuery()
        query.time_range.start_date = "2026-08-01"
        query.time_range.end_date = "2026-08-03"
        assert not evaluate(query, metadata())[0]
        query.time_range.end_date = "2026-08-10"
        assert evaluate(query, metadata())[0]

    def test_a_date_filter_rejects_events_with_no_event_time(self):
        query = ScoutQuery()
        query.time_range.start_date = "2026-08-01"
        matched, reason = evaluate(query, metadata(event_time=None))
        assert not matched
        assert "event_time" in (reason or "")

    def test_scenario_tags_match_on_intersection(self):
        assert evaluate(ScoutQuery(scenario_tags=["BUS_AHEAD_SAME_LANE"]), metadata())[0]
        assert not evaluate(ScoutQuery(scenario_tags=["BUS_AT_STOP"]), metadata())[0]

    def test_every_rejection_carries_a_reason(self):
        matched, reason = evaluate(ScoutQuery(weather=["snow"]), metadata())
        assert not matched
        assert reason and "weather" in reason


# ---------------------------------------------------------------------------
# CSV cell formatting
# ---------------------------------------------------------------------------
class TestCellFormatting:
    def _column(self, column_type: str):
        from backend.configstore import CsvColumn

        return CsvColumn(key="k", header="k", type=column_type, required=False)

    def test_lists_are_joined_with_semicolons(self):
        assert format_cell(["a", "b"], self._column("list")) == "a;b"

    def test_blank_values_render_empty(self):
        assert format_cell(None, self._column("string")) == ""
        assert format_cell([], self._column("list")) == ""

    def test_formula_injection_prefixes_are_escaped(self):
        # A spreadsheet would otherwise execute this on open.
        assert format_cell("=cmd|'/c calc'!A1", self._column("string")).startswith("'=")
        assert format_cell("+1", self._column("string")).startswith("'+")
        assert format_cell("@SUM(A1)", self._column("string")).startswith("'@")

    def test_line_breaks_are_flattened_so_a_row_cannot_split(self):
        assert "\n" not in format_cell("line1\nline2", self._column("string"))

    def test_confidence_is_rendered_at_fixed_precision(self):
        assert format_cell(0.123456, self._column("confidence")) == "0.1235"

    def test_booleans_render_lower_case(self):
        assert format_cell(True, self._column("bool")) == "true"


# ---------------------------------------------------------------------------
# CSV validation
# ---------------------------------------------------------------------------
class TestCsvValidation:
    @pytest.fixture()
    def template(self):
        template = get_config_store().csv_template("germany_bus_test")
        assert template is not None
        return template

    def test_a_complete_row_is_export_ready(self, template):
        readiness, issues = validate_rows([row()], template)
        assert readiness.ready
        assert readiness.blocking_errors == 0
        assert issues == []

    def test_a_missing_mandatory_column_blocks_the_export(self, template):
        readiness, issues = validate_rows([row(rule_version=None)], template)
        assert not readiness.ready
        assert any(issue.rule_id == "CSV_MANDATORY_VALUES" for issue in issues)

    def test_a_value_outside_the_taxonomy_is_rejected(self, template):
        readiness, issues = validate_rows([row(object_type=["spaceship"])], template)
        assert not readiness.ready
        assert any(issue.rule_id == "CSV_ENUM_VALIDITY" for issue in issues)

    def test_duplicate_canonical_keys_are_rejected(self, template):
        readiness, issues = validate_rows([row(), row()], template)
        assert not readiness.ready
        assert any(issue.rule_id == "CSV_DUPLICATE_KEYS" for issue in issues)

    def test_out_of_range_confidence_is_rejected(self, template):
        _, issues = validate_rows([row(polygon_confidence=1.7)], template)
        assert any(issue.rule_id == "CSV_CONFIDENCE_RANGE" for issue in issues)

    def test_out_of_order_timestamps_are_rejected(self, template):
        _, issues = validate_rows(
            [
                row(
                    timestamp_100m="2026-08-05T14:30:10+00:00",
                    timestamp_60m="2026-08-05T14:30:05+00:00",
                )
            ],
            template,
        )
        assert any(issue.rule_id == "CSV_TIMESTAMP_ORDERING" for issue in issues)

    def test_a_country_outside_the_run_filter_is_rejected(self, template):
        _, issues = validate_rows([row(country_code="FR", country="France")], template, expected_country_code="DE")
        assert any(issue.rule_id == "CSV_COUNTRY_CONSISTENCY" for issue in issues)

    def test_country_name_and_code_must_agree(self, template):
        _, issues = validate_rows([row(country="France", country_code="DE")], template)
        assert any(issue.rule_id == "CSV_COUNTRY_CONSISTENCY" for issue in issues)

    def test_rejected_rows_are_separated_and_explained_not_dropped(self, template):
        rows = [row(), row(canonical_event_key="j" * 64, rule_version=None)]
        _, issues = validate_rows(rows, template)
        exportable, rejected = split_rows(rows, issues)
        assert len(exportable) == 1
        assert len(rejected) >= 1
        assert rejected[0]["validation_rule"]
        assert rejected[0]["recommended_correction"]


# ---------------------------------------------------------------------------
# CSV builder
# ---------------------------------------------------------------------------
class TestCsvBuilder:
    def test_an_unknown_template_fails_loudly(self):
        with pytest.raises(ValueError, match="Unknown CSV template"):
            CsvBuilder("does_not_exist")

    def test_mandatory_columns_survive_a_column_selection(self):
        builder = CsvBuilder("germany_bus_test", columns=["country"])
        headers = builder.headers()
        assert "country" in headers
        assert "canonical_event_key" in headers  # required, cannot be dropped
        assert "rule_version" in headers

    def test_writing_produces_a_readable_file(self, tmp_path):
        builder = CsvBuilder("generic_av_event")
        path = tmp_path / "results.csv"
        written = builder.write_csv(path, [row(event_time=NOW.isoformat())])
        assert written == 1
        text = path.read_text(encoding="utf-8-sig")
        assert "canonical_event_key" in text.splitlines()[0]
        assert "DE" in text

    def test_preview_is_limited_and_reports_the_true_total(self):
        builder = CsvBuilder("generic_av_event")
        rows = [row(canonical_event_key=f"{index:064d}", event_time=NOW.isoformat()) for index in range(120)]
        preview = builder.preview(rows, limit=10)
        assert preview["preview_rows"] == 10
        assert preview["total_rows"] == 120
