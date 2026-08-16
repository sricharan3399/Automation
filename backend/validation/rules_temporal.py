"""Temporal validation rules.

Ordering is direction-aware: markers are compared along the direction of
travel, which is the order in which the ego encounters them, not the numeric
order of the distance labels.
"""

from __future__ import annotations

from backend.configstore import RuleDefinition
from backend.models.contracts import ValidationOutcome
from backend.validation.registry import ValidationContext, fail, ok, rule, skip

APPROACH_ORDER = [
    "timestamp_200m",
    "timestamp_100m",
    "timestamp_60m",
    "junction_entry",
    "junction_exit",
]


@rule("TEMPORAL_EVAL_WINDOW_ORDER")
def eval_window_order(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    start = ctx.metadata.evaluation_start
    end = ctx.metadata.evaluation_end
    if start is None or end is None:
        return skip(definition, "The event has no evaluation window in its metadata.")
    if start < end:
        return ok(definition, f"Evaluation window {start.isoformat()} -> {end.isoformat()} is ordered.")
    return fail(
        definition,
        f"Evaluation window starts at {start.isoformat()} which is not before its end {end.isoformat()}.",
        correction="Correct evaluation_start / evaluation_end in the source metadata, or re-export the event.",
        observed={"evaluation_start": start.isoformat(), "evaluation_end": end.isoformat()},
    )


@rule("TEMPORAL_EVENT_IN_WINDOW")
def event_in_window(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    start = ctx.metadata.evaluation_start
    end = ctx.metadata.evaluation_end
    event_time = ctx.metadata.event_time
    if event_time is None:
        return skip(definition, "The event has no event_time.")
    if start is None or end is None:
        return skip(definition, "The event has no evaluation window to test against.")
    low, high = (start, end) if start <= end else (end, start)
    if low <= event_time <= high:
        return ok(definition, "The event time falls inside the evaluation window.")
    return fail(
        definition,
        f"Event time {event_time.isoformat()} lies outside the evaluation window "
        f"{low.isoformat()} -> {high.isoformat()}.",
        correction="Widen the evaluation window or correct the event time in the source metadata.",
        observed={
            "event_time": event_time.isoformat(),
            "evaluation_start": low.isoformat(),
            "evaluation_end": high.isoformat(),
        },
    )


@rule("TEMPORAL_DISTANCE_MARKER_ORDER")
def distance_marker_order(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    available = [(name, ctx.marker_t(name)) for name in APPROACH_ORDER]
    present = [(name, t) for name, t in available if t is not None]
    if len(present) < 2:
        return skip(
            definition,
            "Fewer than two approach markers could be derived, so their ordering cannot be checked.",
        )

    violations: list[str] = []
    for (name_a, t_a), (name_b, t_b) in zip(present, present[1:], strict=False):
        if t_a > t_b:
            violations.append(f"{name_a} ({t_a:.3f}s) occurs after {name_b} ({t_b:.3f}s)")

    if not violations:
        return ok(
            definition,
            "Approach markers are ordered along the direction of travel: "
            + " < ".join(f"{name} {t:.2f}s" for name, t in present),
            observed=dict(present),
        )
    return fail(
        definition,
        "Approach markers are out of order: " + "; ".join(violations) + ".",
        correction=(
            "Check the junction polygon and the trajectory direction. An out-of-order approach "
            "usually means the wrong junction was selected or the trajectory is reversed."
        ),
        observed=dict(present),
    )


@rule("TEMPORAL_ENTRY_BEFORE_EXIT")
def entry_before_exit(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    entry = ctx.marker_t("junction_entry")
    exit_t = ctx.marker_t("junction_exit")
    if entry is None or exit_t is None:
        return skip(definition, "Junction entry and/or exit could not be derived.")
    if entry < exit_t:
        return ok(definition, f"Junction entry {entry:.3f}s precedes exit {exit_t:.3f}s.")
    return fail(
        definition,
        f"Junction entry {entry:.3f}s does not precede exit {exit_t:.3f}s.",
        correction="Re-check the junction polygon; the entry and exit crossings may be swapped.",
        observed={"junction_entry": entry, "junction_exit": exit_t},
    )


@rule("TEMPORAL_WAIT_LINE_BEFORE_ENTRY")
def wait_line_before_entry(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    wait = ctx.marker_t("wait_line_crossing")
    entry = ctx.marker_t("junction_entry")
    if wait is None or entry is None:
        return skip(definition, "The wait-line crossing and/or junction entry could not be derived.")
    if wait <= entry + 1e-6:
        return ok(definition, f"Wait line crossed at {wait:.3f}s, before junction entry at {entry:.3f}s.")
    return fail(
        definition,
        f"Wait line crossed at {wait:.3f}s, after junction entry at {entry:.3f}s.",
        correction=(
            "The mapped stop line may belong to a different approach, or the junction polygon "
            "may extend past the stop line."
        ),
        observed={"wait_line_crossing": wait, "junction_entry": entry},
    )


@rule("TEMPORAL_FIRST_SEEN_BEFORE_FULL_VIEW")
def first_seen_before_full_view(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    first = ctx.scene.first_visible_t
    full = ctx.scene.full_view_t
    if first is None or full is None:
        return skip(definition, "First-visible and/or full-view instants were not determined.")
    if first <= full + 1e-6:
        return ok(definition, f"First visible {first:.3f}s is at or before full view {full:.3f}s.")
    return fail(
        definition,
        f"First visible {first:.3f}s occurs after full view {full:.3f}s.",
        correction="Review the detection sequence; the visibility instants are inconsistent.",
        observed={"first_visible": first, "full_view": full},
    )


@rule("TEMPORAL_WITHIN_SOURCE_DURATION")
def within_source_duration(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    end = ctx.source_end_t
    if end is None:
        return skip(definition, "The source did not report a clip duration.")
    start = ctx.source_start_t

    markers = ctx.all_marker_times()
    if ctx.scene.first_visible_t is not None:
        markers["first_visible"] = ctx.scene.first_visible_t
    if ctx.scene.full_view_t is not None:
        markers["full_view"] = ctx.scene.full_view_t
    if not markers:
        return skip(definition, "No timestamps were derived for this event.")

    outside = {name: t for name, t in markers.items() if t < start - 1e-6 or t > end + 1e-6}
    if not outside:
        return ok(
            definition,
            f"All {len(markers)} derived timestamps lie within the recorded clip "
            f"({start:.2f}s - {end:.2f}s).",
        )
    return fail(
        definition,
        "Derived timestamps fall outside the recorded clip "
        f"({start:.2f}s - {end:.2f}s): "
        + ", ".join(f"{name}={t:.3f}s" for name, t in sorted(outside.items()))
        + ".",
        correction=(
            "The clip does not cover the full scenario. Re-export a longer window, or mark the "
            "affected markers as not applicable."
        ),
        observed={"source_start_t": start, "source_end_t": end, "outside": outside},
    )
