"""Duplicate detection.

The canonical event key is deterministic, so re-processing an event produces a
match rather than a second row. The pipeline UPSERTS the existing record and
preserves reviewer comments and decisions; this rule records that it happened.
"""

from __future__ import annotations

from backend.configstore import RuleDefinition
from backend.models.contracts import ValidationOutcome
from backend.validation.registry import ValidationContext, ok, rule


@rule("DUPLICATE_CANONICAL_KEY")
def duplicate_canonical_key(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    if not ctx.duplicate_exists:
        return ok(
            definition,
            f"canonical_event_key {ctx.canonical_event_key[:12]}… is new; a record will be created.",
            observed={"canonical_event_key": ctx.canonical_event_key, "action": "insert"},
        )
    return ok(
        definition,
        f"canonical_event_key {ctx.canonical_event_key[:12]}… already exists; the existing record is "
        "updated in place and reviewer decisions are preserved.",
        observed={"canonical_event_key": ctx.canonical_event_key, "action": "upsert"},
    )
