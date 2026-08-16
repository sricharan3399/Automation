"""Export-readiness validation.

Runs the ``CSV_*`` rules over the assembled export rows before anything is
written. A row with an unresolved blocking issue is never written into
``results.csv``; it goes to ``rejected_records.csv`` with the rule that
rejected it and a recommended correction, so nothing disappears silently.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from typing import Any

from backend.configstore import CsvColumn, CsvTemplate, RuleDefinition, get_config_store
from backend.models.contracts import ExportReadiness, Severity
from backend.settings import get_settings

#: Values starting with these characters are interpreted as formulas by
#: spreadsheet applications; they are escaped rather than written raw.
CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

TIMESTAMP_ORDER = [
    "timestamp_200m",
    "timestamp_100m",
    "timestamp_60m",
    "junction_entry_time",
    "junction_exit_time",
    "post_junction_20m_time",
]


class RowIssue:
    def __init__(
        self,
        row_index: int,
        canonical_event_key: str,
        rule_id: str,
        severity: str,
        message: str,
        correction: str | None = None,
        column: str | None = None,
    ) -> None:
        self.row_index = row_index
        self.canonical_event_key = canonical_event_key
        self.rule_id = rule_id
        self.severity = severity
        self.message = message
        self.correction = correction
        self.column = column

    @property
    def blocking(self) -> bool:
        return self.severity in ("BLOCKING", "ERROR")

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "canonical_event_key": self.canonical_event_key,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "recommended_correction": self.correction,
            "column": self.column,
        }


def _rules() -> dict[str, RuleDefinition]:
    return {r.id: r for r in get_config_store().rules() if r.category == "CSV" and r.enabled}


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def format_cell(value: Any, column: CsvColumn, encoding: str = "utf-8") -> str:
    """Render one value for CSV, escaping formula-injection prefixes."""
    if _is_blank(value):
        return ""
    if column.type == "list" and isinstance(value, (list, tuple, set)):
        text = ";".join(str(v) for v in value)
    elif column.type == "bool":
        text = "true" if bool(value) else "false"
    elif column.type == "datetime" and isinstance(value, datetime):
        text = value.isoformat()
    elif column.type == "confidence" and isinstance(value, (int, float)):
        text = f"{float(value):.4f}"
    elif column.type in ("float", "duration") and isinstance(value, (int, float)):
        text = f"{float(value):.3f}"
    else:
        text = str(value)

    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if text.startswith(CSV_INJECTION_PREFIXES):
        text = "'" + text
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def validate_rows(
    rows: list[dict[str, Any]],
    template: CsvTemplate,
    encoding: str = "utf-8-sig",
    expected_country_code: str | None = None,
) -> tuple[ExportReadiness, list[RowIssue]]:
    """Validate every row against the enabled CSV rules."""
    rules = _rules()
    issues: list[RowIssue] = []
    taxonomy = get_config_store()
    seen_keys: dict[str, int] = {}

    def add(rule_id: str, index: int, row: dict[str, Any], message: str, correction: str, column: str | None = None) -> None:
        definition = rules.get(rule_id)
        if definition is None:
            return
        issues.append(
            RowIssue(
                row_index=index,
                canonical_event_key=str(row.get("canonical_event_key", "")),
                rule_id=rule_id,
                severity=definition.severity,
                message=message,
                correction=correction,
                column=column,
            )
        )

    for index, row in enumerate(rows):
        # --- CSV_MANDATORY_VALUES -------------------------------------
        for column in template.columns:
            if column.required and _is_blank(row.get(column.key)):
                add(
                    "CSV_MANDATORY_VALUES",
                    index,
                    row,
                    f"Mandatory column '{column.header}' is empty.",
                    "Complete this field in the Review Queue before exporting.",
                    column.header,
                )

        # --- CSV_ENUM_VALIDITY ------------------------------------------
        for column in template.columns:
            if not column.enum:
                continue
            value = row.get(column.key)
            if _is_blank(value):
                continue
            candidates = value if isinstance(value, (list, tuple, set)) else [value]
            for candidate in candidates:
                if not taxonomy.is_valid_taxonomy_value(column.enum, str(candidate)):
                    add(
                        "CSV_ENUM_VALIDITY",
                        index,
                        row,
                        f"Column '{column.header}' holds '{candidate}', which is not in the "
                        f"'{column.enum}' vocabulary.",
                        "Correct the value, or add it to the source taxonomy mapping.",
                        column.header,
                    )

        # --- CSV_TIMESTAMP_ORDERING ---------------------------------------
        parsed = [
            (name, _parse_datetime(row.get(name)))
            for name in TIMESTAMP_ORDER
            if not _is_blank(row.get(name))
        ]
        present: list[tuple[str, datetime]] = [
            (name, value) for name, value in parsed if value is not None
        ]
        for (name_a, value_a), (name_b, value_b) in zip(present, present[1:], strict=False):
            if value_a > value_b:
                add(
                    "CSV_TIMESTAMP_ORDERING",
                    index,
                    row,
                    f"Timestamp '{name_a}' ({value_a.isoformat()}) is after '{name_b}' "
                    f"({value_b.isoformat()}).",
                    "Re-derive the markers, or correct them in the Review Queue.",
                    name_b,
                )

        # --- CSV_DUPLICATE_KEYS -------------------------------------------
        key = str(row.get("canonical_event_key", ""))
        if key:
            if key in seen_keys:
                add(
                    "CSV_DUPLICATE_KEYS",
                    index,
                    row,
                    f"canonical_event_key {key[:12]}… already appears at row {seen_keys[key]}.",
                    "Two records resolved to the same identity; merge them before exporting.",
                    "canonical_event_key",
                )
            else:
                seen_keys[key] = index

        # --- CSV_CONFIDENCE_RANGE -------------------------------------------
        for column in template.columns:
            if column.type != "confidence":
                continue
            value = row.get(column.key)
            if _is_blank(value):
                continue
            try:
                numeric = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                add(
                    "CSV_CONFIDENCE_RANGE",
                    index,
                    row,
                    f"Confidence column '{column.header}' holds a non-numeric value '{value}'.",
                    "Confidence must be a number between 0.0 and 1.0.",
                    column.header,
                )
                continue
            if not 0.0 <= numeric <= 1.0:
                add(
                    "CSV_CONFIDENCE_RANGE",
                    index,
                    row,
                    f"Confidence column '{column.header}' is {numeric}, outside 0.0-1.0.",
                    "Confidence must be a number between 0.0 and 1.0.",
                    column.header,
                )

        # --- CSV_COUNTRY_CONSISTENCY -----------------------------------------
        country = row.get("country")
        code = row.get("country_code")
        if not _is_blank(code) and expected_country_code and str(code).upper() != expected_country_code.upper():
            add(
                "CSV_COUNTRY_CONSISTENCY",
                index,
                row,
                f"country_code '{code}' does not match the run filter '{expected_country_code}'.",
                "Remove the out-of-scope record, or correct the run's country filter.",
                "country_code",
            )
        if not _is_blank(country) and not _is_blank(code):
            allowed = {
                str(entry.get("name", "")).lower(): str(entry.get("code", "")).upper()
                for entry in get_settings().raw.get("countries", {}).get("allowed", []) or []
            }
            expected = allowed.get(str(country).lower())
            if expected and expected != str(code).upper():
                add(
                    "CSV_COUNTRY_CONSISTENCY",
                    index,
                    row,
                    f"country '{country}' and country_code '{code}' disagree.",
                    "Correct the country mapping on the Connections page.",
                    "country_code",
                )

        # --- CSV_ENCODING_AND_ESCAPING ----------------------------------------
        for column in template.columns:
            value = row.get(column.key)
            if _is_blank(value):
                continue
            rendered = format_cell(value, column, encoding)
            try:
                rendered.encode(encoding)
            except (UnicodeEncodeError, LookupError):
                add(
                    "CSV_ENCODING_AND_ESCAPING",
                    index,
                    row,
                    f"Column '{column.header}' cannot be encoded as {encoding}.",
                    "Remove the unsupported characters, or change the export encoding.",
                    column.header,
                )
            if re.search(r"[\r\n]", str(value)):
                add(
                    "CSV_ENCODING_AND_ESCAPING",
                    index,
                    row,
                    f"Column '{column.header}' contains a line break, which would break the row.",
                    "Line breaks are stripped on export; confirm the resulting value is still correct.",
                    column.header,
                )

    blocking = [i for i in issues if i.blocking]
    warnings = [i for i in issues if not i.blocking]
    blocked_rows = {i.row_index for i in blocking}

    readiness = ExportReadiness(
        passed=len(rows) - len(blocked_rows),
        warnings=len(warnings),
        blocking_errors=len(blocking),
        ready=not blocking,
        total_rows=len(rows),
        exportable_rows=len(rows) - len(blocked_rows),
        rejected_rows=len(blocked_rows),
        issues=[i.to_dict() for i in issues],
    )
    return readiness, issues


def split_rows(
    rows: list[dict[str, Any]], issues: list[RowIssue]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into exportable rows and rejected records.

    Rejected records carry the rule that rejected them and the recommended
    correction so the tester can fix the underlying data.
    """
    blocking_by_row: dict[int, list[RowIssue]] = {}
    for issue in issues:
        if issue.blocking:
            blocking_by_row.setdefault(issue.row_index, []).append(issue)

    exportable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_issues = blocking_by_row.get(index)
        if not row_issues:
            exportable.append(row)
            continue
        for issue in row_issues:
            rejected.append(
                {
                    "canonical_event_key": row.get("canonical_event_key", ""),
                    "event_reference": row.get("anonymized_event_ref", ""),
                    "validation_rule": issue.rule_id,
                    "failure_reason": issue.message,
                    "recommended_correction": issue.correction or "",
                    "severity": issue.severity,
                    "column": issue.column or "",
                }
            )
    return exportable, rejected


def write_rejected_records(path: Any, rejected: list[dict[str, Any]], encoding: str = "utf-8-sig") -> int:
    fields = [
        "canonical_event_key",
        "event_reference",
        "validation_rule",
        "failure_reason",
        "recommended_correction",
        "severity",
        "column",
    ]
    with open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in rejected:
            writer.writerow({key: record.get(key, "") for key in fields})
    return len(rejected)


def severity_of(rule_id: str) -> Severity:
    definition = _rules().get(rule_id)
    return Severity(definition.severity) if definition else Severity.WARNING
