"""Rules that promote engine findings into validation outcomes.

The perception, tracking and behaviour engines produce CANDIDATE findings. This
module turns each finding code into a validation outcome so it appears on the
QA report, in the review queue and in the exported abnormality categories -
still as a candidate, never as a confirmed defect.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.configstore import RuleDefinition
from backend.models.contracts import SceneFinding, ValidationOutcome
from backend.validation.registry import ValidationContext, fail, ok, rule, skip

PERCEPTION_CODES = [
    "BUS_MISSED_DETECTION",
    "BUS_FALSE_POSITIVE",
    "BUS_WRONG_CLASSIFICATION",
    "LOW_CONFIDENCE_BUS",
]
TRACKING_CODES = [
    "BUS_TRACK_ID_SWITCH",
    "BUS_TRACK_LOSS",
    "BUS_TRACK_FRAGMENTATION",
    "BUS_DUPLICATE_TRACK",
    "BUS_TEMPORARY_LOSS",
]
BEHAVIOR_CODES = ["BEHAVIOR_ROLLING_STOP_CANDIDATE"]


def _all_findings(ctx: ValidationContext) -> list[SceneFinding]:
    return [
        *ctx.scene.perception_findings,
        *ctx.scene.tracking_findings,
        *ctx.behavior.findings,
    ]


def _make_rule(code: str, needs_reference: bool = False) -> Callable[[ValidationContext, RuleDefinition], ValidationOutcome]:
    def implementation(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
        if not ctx.scene.available and code in PERCEPTION_CODES + TRACKING_CODES:
            return skip(
                definition,
                ctx.scene.unavailable_reason or "Scene analysis did not run for this event.",
            )
        if needs_reference and not ctx.scene.reference_data_available:
            return skip(
                definition,
                "No reference annotations are available, so this comparison could not be made. "
                "This is not evidence that perception was correct.",
            )

        matches = [f for f in _all_findings(ctx) if f.code == code]
        if not matches:
            return ok(definition, f"No {code} candidates were found.")

        first = matches[0]
        detail = matches[0].message if len(matches) == 1 else (
            f"{len(matches)} occurrences; first: {first.message}"
        )
        return fail(
            definition,
            detail,
            correction=(
                "Review the evidence for this interval and confirm or reject the candidate. "
                "The platform does not classify this as a defect on its own."
            ),
            observed={
                "occurrences": len(matches),
                "findings": [
                    {
                        "t": f.t,
                        "track_id": f.track_id,
                        "camera": f.camera,
                        "message": f.message,
                        "evidence": f.evidence,
                    }
                    for f in matches[:10]
                ],
            },
        )

    implementation.__name__ = f"finding_rule_{code.lower()}"
    return implementation


for _code in PERCEPTION_CODES:
    rule(_code)(_make_rule(_code, needs_reference=_code != "LOW_CONFIDENCE_BUS"))

for _code in TRACKING_CODES:
    rule(_code)(_make_rule(_code))

for _code in BEHAVIOR_CODES:
    rule(_code)(_make_rule(_code))


@rule("BEHAVIOR_STOP_OBSERVATION")
def stop_observation(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    """Record the measured stopping behaviour. Always informational.

    This rule never fails. It exists so the measurement is captured verbatim on
    the QA report without any judgement attached to it.
    """
    if not ctx.behavior.available:
        return skip(definition, ctx.behavior.unavailable_reason or "Behaviour analysis did not run.")
    observations = [o for o in ctx.behavior.observations if o.name in ("below_stop_threshold", "minimum_speed")]
    return ok(
        definition,
        " ".join(o.observation for o in observations) or "No stop-related observation was produced.",
        observed={
            "stop_classification": ctx.behavior.stop_classification,
            "minimum_speed_mps": ctx.behavior.minimum_speed_mps,
            "stop_duration_s": ctx.behavior.stop_duration_s,
            "wait_line_distance_m": ctx.behavior.wait_line_distance_m,
            "note": "Observation only. Interpretation is a human decision.",
        },
    )
