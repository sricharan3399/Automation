"""Validation rule engine.

Runs every enabled rule from ``config/validation_rules.yaml`` against one
event's :class:`ValidationContext` and returns a :class:`ValidationReport`.

Three states are kept strictly distinct:

    passed   - the rule ran and the condition held
    failed   - the rule ran and the condition did not hold
    skipped  - the rule could not run, with a stated reason

A rule waiting on an approved project threshold is always *skipped with that
reason*, never silently passed. That difference is what stops a QA report from
implying coverage the run did not have.
"""

from __future__ import annotations

import logging

from backend.configstore import RuleDefinition, get_config_store
from backend.models.contracts import ValidationOutcome, ValidationReport
from backend.validation import (  # noqa: F401  (imported for registration side effects)
    rules_duplicate,
    rules_findings,
    rules_geometry,
    rules_sensor,
    rules_temporal,
)
from backend.validation.registry import ValidationContext, registry, skip

log = logging.getLogger(__name__)

CSV_CATEGORY = "CSV"


def applicable_rules(rule_overrides: dict[str, bool] | None = None) -> list[RuleDefinition]:
    """Rules that will be attempted for a per-event run.

    CSV rules are excluded: they operate on assembled export rows, not on a
    single event, and are run by :mod:`backend.reports.csv_validation`.
    """
    overrides = rule_overrides or {}
    out: list[RuleDefinition] = []
    for definition in get_config_store().rules():
        if definition.category == CSV_CATEGORY:
            continue
        enabled = overrides.get(definition.id, definition.enabled)
        if enabled:
            out.append(definition)
    return out


def run_rules(
    ctx: ValidationContext,
    rule_overrides: dict[str, bool] | None = None,
) -> ValidationReport:
    """Execute every applicable rule and collect the outcomes."""
    store = get_config_store()
    implementations = registry()
    outcomes: list[ValidationOutcome] = []

    for definition in applicable_rules(rule_overrides):
        if definition.awaiting_project_threshold:
            outcomes.append(
                skip(
                    definition,
                    "This rule requires an approved project threshold, which has not been supplied. "
                    "The platform will not evaluate it against an invented value.",
                )
            )
            continue

        if definition.requires_reference_data and not ctx.scene.reference_data_available:
            outcomes.append(
                skip(
                    definition,
                    "This rule needs reference annotations, which are not available for this event.",
                )
            )
            continue

        implementation = implementations.get(definition.id)
        if implementation is None:
            outcomes.append(
                skip(
                    definition,
                    "No implementation for this rule exists in this build. It is listed in the "
                    "catalogue but was not evaluated.",
                )
            )
            continue

        try:
            result = implementation(ctx, definition)
        except Exception as exc:  # a broken rule must not abort the whole event
            log.exception("Validation rule %s raised", definition.id)
            outcomes.append(
                skip(definition, f"The rule implementation raised an error and was skipped: {exc}")
            )
            continue

        if result is None:
            outcomes.append(skip(definition, "The rule returned no result."))
        elif isinstance(result, list):
            outcomes.extend(result)
        else:
            outcomes.append(result)

    return ValidationReport(outcomes=outcomes, rule_version=store.rule_version_signature())


#: Rule-catalogue categories and CSV abnormality categories are two different
#: vocabularies. Rules are grouped for the tester ("Temporal"); the CSV records
#: the controlled abnormality vocabulary ("TIMESTAMP"). Anything not listed here
#: uses the same name in both.
RULE_CATEGORY_TO_ABNORMALITY = {"TEMPORAL": "TIMESTAMP"}


def abnormality_categories(report: ValidationReport) -> list[str]:
    """Distinct abnormality categories that produced at least one failure."""
    return sorted(
        {
            RULE_CATEGORY_TO_ABNORMALITY.get(outcome.category, outcome.category)
            for outcome in report.failures
        }
    )


def coverage_summary(report: ValidationReport) -> dict[str, object]:
    """What the run actually checked, for the QA report."""
    skipped = [o for o in report.outcomes if o.skipped]
    return {
        "rules_attempted": len(report.outcomes),
        "rules_evaluated": len(report.outcomes) - len(skipped),
        "rules_skipped": len(skipped),
        "skipped": [{"rule_id": o.rule_id, "reason": o.skip_reason} for o in skipped],
        "counts": report.counts(),
    }
