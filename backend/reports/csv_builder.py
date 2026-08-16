"""CSV assembly and run-output writing.

Produces the full run directory::

    run_<timestamp>/
      results.csv
      rejected_records.csv
      summary.json
      validation_report.json
      run_config.json
      evidence_manifest.csv
      audit.jsonl
      evidence/

``results.csv`` is only written when export readiness has no blocking errors;
otherwise the run reports NOT READY and the blocked rows appear in
``rejected_records.csv`` with the rule that rejected them.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.configstore import CsvTemplate, get_config_store
from backend.models.contracts import ExportReadiness, ReviewPackage
from backend.reports.csv_validation import (
    RowIssue,
    format_cell,
    split_rows,
    validate_rows,
    write_rejected_records,
)
from backend.settings import get_settings
from backend.version import CONTRACT_VERSION, METHOD_VERSION, SOFTWARE_VERSION

log = logging.getLogger(__name__)


class CsvBuilder:
    """Builds and writes the export artefacts for one run."""

    def __init__(self, template_id: str = "germany_bus_test", columns: list[str] | None = None) -> None:
        store = get_config_store()
        template = store.csv_template(template_id)
        if template is None:
            available = ", ".join(sorted(store.csv_templates()))
            raise ValueError(f"Unknown CSV template '{template_id}'. Available: {available}")
        self.template: CsvTemplate = template
        self.selected_columns = columns or [c.key for c in template.columns]
        self.export_config = get_settings().section("export")

    # -- column selection --------------------------------------------------
    @property
    def columns(self) -> list[Any]:
        chosen = set(self.selected_columns)
        # Mandatory columns can never be dropped by a column selection.
        return [c for c in self.template.columns if c.key in chosen or c.required]

    def headers(self) -> list[str]:
        return [c.header for c in self.columns]

    # -- rendering ---------------------------------------------------------
    def render_row(self, row: dict[str, Any]) -> dict[str, str]:
        encoding = str(self.export_config.get("encoding", "utf-8-sig"))
        return {c.header: format_cell(row.get(c.key), c, encoding) for c in self.columns}

    def preview(self, rows: list[dict[str, Any]], limit: int | None = None) -> dict[str, Any]:
        count = limit or int(self.export_config.get("preview_rows", 50))
        return {
            "template": self.template.to_dict(),
            "headers": self.headers(),
            "rows": [self.render_row(row) for row in rows[:count]],
            "total_rows": len(rows),
            "preview_rows": min(count, len(rows)),
        }

    # -- validation --------------------------------------------------------
    def validate(
        self, rows: list[dict[str, Any]], expected_country_code: str | None = None
    ) -> tuple[ExportReadiness, list[RowIssue]]:
        return validate_rows(
            rows,
            self.template,
            encoding=str(self.export_config.get("encoding", "utf-8-sig")),
            expected_country_code=expected_country_code,
        )

    # -- writing -----------------------------------------------------------
    def write_csv(self, path: Path, rows: list[dict[str, Any]]) -> int:
        encoding = str(self.export_config.get("encoding", "utf-8-sig"))
        delimiter = str(self.export_config.get("delimiter", ","))
        terminator = str(self.export_config.get("line_terminator", "\n"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=self.headers(),
                delimiter=delimiter,
                lineterminator=terminator,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(self.render_row(row))
        return len(rows)


def new_run_directory(run_id: str, base: Path | None = None) -> Path:
    root = base or get_settings().output_dir
    directory = root / f"run_{run_id}"
    (directory / "evidence").mkdir(parents=True, exist_ok=True)
    return directory


def write_evidence_manifest(path: Path, packages: list[ReviewPackage], encoding: str = "utf-8-sig") -> int:
    fields = [
        "evidence_id",
        "canonical_event_key",
        "event_reference",
        "purpose",
        "kind",
        "camera",
        "relative_timestamp_s",
        "relative_path",
        "content_hash",
        "redacted",
        "approved",
        "available",
        "unavailable_reason",
    ]
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for package in packages:
            for item in package.evidence:
                writer.writerow(
                    {
                        "evidence_id": item.evidence_id,
                        "canonical_event_key": package.canonical_event_key,
                        "event_reference": package.anonymized_event_ref,
                        "purpose": item.purpose,
                        "kind": item.kind,
                        "camera": item.camera or "",
                        "relative_timestamp_s": "" if item.t_rel_s is None else f"{item.t_rel_s:.3f}",
                        "relative_path": item.relative_path,
                        "content_hash": item.content_hash or "",
                        "redacted": "true" if item.redacted else "false",
                        "approved": "true" if item.approved else "false",
                        "available": "true" if item.available else "false",
                        "unavailable_reason": item.unavailable_reason or "",
                    }
                )
                count += 1
    return count


def write_validation_report(path: Path, packages: list[ReviewPackage]) -> dict[str, Any]:
    """Per-event rule outcomes plus an aggregate coverage summary."""
    by_rule: dict[str, dict[str, int]] = {}
    events: list[dict[str, Any]] = []

    for package in packages:
        for outcome in package.validation.outcomes:
            bucket = by_rule.setdefault(
                outcome.rule_id, {"passed": 0, "failed": 0, "skipped": 0}
            )
            if outcome.skipped:
                bucket["skipped"] += 1
            elif outcome.passed:
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1

        events.append(
            {
                "canonical_event_key": package.canonical_event_key,
                "event_reference": package.anonymized_event_ref,
                "status": package.status.value,
                "blocking_error_count": package.blocking_error_count,
                "overall_confidence": package.overall_confidence,
                "abnormality_categories": package.abnormality_categories,
                "outcomes": [
                    {
                        "rule_id": o.rule_id,
                        "category": o.category,
                        "severity": o.severity.value,
                        "passed": o.passed,
                        "skipped": o.skipped,
                        "skip_reason": o.skip_reason,
                        "message": o.message,
                        "recommended_correction": o.recommended_correction,
                        "rule_version": o.rule_version,
                    }
                    for o in package.validation.outcomes
                ],
            }
        )

    report = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule_version": get_config_store().rule_version_signature(),
        "rule_totals": by_rule,
        "events": events,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return report


def write_summary(
    path: Path,
    run_id: str,
    packages: list[ReviewPackage],
    readiness: ExportReadiness,
    counters: dict[str, Any],
    elapsed_s: float,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for package in packages:
        status_counts[package.status.value] = status_counts.get(package.status.value, 0) + 1

    category_counts: dict[str, int] = {}
    for package in packages:
        for category in package.abnormality_categories:
            category_counts[category] = category_counts.get(category, 0) + 1

    confidences = [p.overall_confidence for p in packages if p.overall_confidence]
    summary = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_s, 3),
        "counters": counters,
        "records": {
            "total": len(packages),
            "by_status": status_counts,
            "mean_overall_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        },
        "abnormality_categories": category_counts,
        "export_readiness": readiness.model_dump(mode="json"),
        "versions": {
            "software": SOFTWARE_VERSION,
            "contract": CONTRACT_VERSION,
            "method": METHOD_VERSION,
            "rules": get_config_store().rule_version_signature(),
        },
        "human_review": {
            "required": True,
            "note": (
                "Machine findings are candidates. No record is a confirmed defect without a "
                "reviewer decision recorded in the review history."
            ),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return summary


def write_run_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_outputs(
    run_dir: Path,
    run_id: str,
    packages: list[ReviewPackage],
    rows: list[dict[str, Any]],
    builder: CsvBuilder,
    run_config: dict[str, Any],
    counters: dict[str, Any],
    elapsed_s: float,
    expected_country_code: str | None = None,
) -> dict[str, Any]:
    """Write every run artefact and return a manifest of what was produced."""
    readiness, issues = builder.validate(rows, expected_country_code)
    exportable, rejected = split_rows(rows, issues)
    encoding = str(builder.export_config.get("encoding", "utf-8-sig"))
    block_on_errors = bool(builder.export_config.get("block_export_on_blocking_errors", True))

    results_path = run_dir / "results.csv"
    rows_written = 0
    if readiness.ready or not block_on_errors:
        rows_written = builder.write_csv(results_path, exportable)
    elif exportable:
        # Even when the run is NOT READY the clean rows are written, clearly
        # separated, so a partial failure does not block the whole batch.
        results_path = run_dir / "results_partial.csv"
        rows_written = builder.write_csv(results_path, exportable)

    rejected_count = write_rejected_records(run_dir / "rejected_records.csv", rejected, encoding)
    evidence_count = write_evidence_manifest(run_dir / "evidence_manifest.csv", packages, encoding)
    write_validation_report(run_dir / "validation_report.json", packages)
    write_run_config(run_dir / "run_config.json", run_config)
    counters = {**counters, "csv_rows_created": rows_written, "rejected_records": rejected_count}
    summary = write_summary(run_dir / "summary.json", run_id, packages, readiness, counters, elapsed_s)

    return {
        "run_dir": str(run_dir),
        "results_csv": str(results_path) if rows_written else None,
        "results_is_partial": results_path.name == "results_partial.csv",
        "rejected_records_csv": str(run_dir / "rejected_records.csv"),
        "evidence_manifest_csv": str(run_dir / "evidence_manifest.csv"),
        "validation_report_json": str(run_dir / "validation_report.json"),
        "run_config_json": str(run_dir / "run_config.json"),
        "summary_json": str(run_dir / "summary.json"),
        "rows_written": rows_written,
        "rejected_records": rejected_count,
        "evidence_records": evidence_count,
        "readiness": readiness.model_dump(mode="json"),
        "summary": summary,
    }
