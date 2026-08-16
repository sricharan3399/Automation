"""Sensor availability, synchronisation, data-completeness and metadata rules."""

from __future__ import annotations

from backend.configstore import RuleDefinition, get_config_store
from backend.connectors.normalization import is_authoritative_country_source
from backend.connectors.sensors import missing_required_streams
from backend.models.contracts import StreamRequirement, ValidationOutcome
from backend.settings import get_settings
from backend.validation.registry import ValidationContext, fail, ok, rule, skip


def _issue_streams(ctx: ValidationContext, issue: str) -> list[str]:
    return [h.key for h in ctx.sync.stream_health if issue in h.issues]


# ---------------------------------------------------------------------------
# Sensor availability
# ---------------------------------------------------------------------------
@rule("SENSOR_REQUIRED_STREAM_PRESENT")
def required_streams_present(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    required = ctx.sensor_config.required_keys()
    if not required:
        return skip(definition, "No streams were marked Required in the sensor configuration.")
    missing = missing_required_streams(ctx.bundle.streams, ctx.sensor_config)
    if not missing:
        return ok(
            definition,
            f"All {len(required)} required stream(s) are present: {', '.join(required)}.",
            observed={"required": required},
        )
    return fail(
        definition,
        "Required sensor stream(s) missing from the source: " + ", ".join(missing) + ".",
        correction=(
            "Either the export is incomplete, or these streams should not be marked Required for "
            "this dataset. Adjust the Sensor Configuration or request a complete export."
        ),
        observed={"missing": missing, "required": required},
    )


@rule("SENSOR_STREAM_AVAILABILITY")
def stream_availability(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    minimum = float(get_settings().section("synchronization").get("min_stream_availability_pct", 95.0))
    considered = [
        h
        for h in ctx.sync.stream_health
        if h.requirement == StreamRequirement.REQUIRED and h.availability_pct is not None
    ]
    if not considered:
        return skip(definition, "No required stream reported a measurable availability percentage.")
    below = [h for h in considered if h.availability_pct is not None and h.availability_pct < minimum]
    if not below:
        worst = min(h.availability_pct for h in considered if h.availability_pct is not None)
        return ok(
            definition,
            f"All required streams meet the {minimum:.0f}% availability floor (lowest {worst:.1f}%).",
            observed={"minimum_pct": minimum, "lowest_pct": worst},
        )
    return fail(
        definition,
        "Required stream(s) below the availability floor: "
        + ", ".join(f"{h.key} at {h.availability_pct:.1f}%" for h in below)
        + f" (floor {minimum:.0f}%).",
        correction="Check the recording for dropped frames or a truncated export.",
        observed={"below": [{"stream": h.key, "availability_pct": h.availability_pct} for h in below]},
    )


@rule("SENSOR_FROZEN_STREAM")
def frozen_stream(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    frozen = _issue_streams(ctx, "FROZEN_VIDEO") + _issue_streams(ctx, "FROZEN_STREAM")
    mandatory = {
        h.key for h in ctx.sync.stream_health if h.requirement == StreamRequirement.REQUIRED
    }
    frozen_mandatory = [key for key in frozen if key in mandatory]
    if not frozen:
        return ok(definition, "No stream shows a frozen payload signature.")
    if not frozen_mandatory:
        return fail(
            definition,
            "Optional stream(s) appear frozen: " + ", ".join(frozen) + ".",
            correction="Review the recording; a frozen optional stream still affects evidence quality.",
            observed={"frozen": frozen},
        )
    return fail(
        definition,
        "Mandatory stream(s) appear frozen: " + ", ".join(frozen_mandatory) + ".",
        correction=(
            "A frozen mandatory stream makes the clip unusable for timing analysis. Re-export the "
            "event or mark it as a data error."
        ),
        observed={"frozen": frozen, "mandatory_frozen": frozen_mandatory},
    )


@rule("SENSOR_DROPPED_FRAMES")
def dropped_frames(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    affected = _issue_streams(ctx, "DROPPED_FRAMES")
    if not affected:
        return ok(definition, "No camera stream reports dropped frames.")
    return fail(
        definition,
        "Dropped frames detected on: " + ", ".join(affected) + ".",
        correction="Confirm the sample rate declared by the source matches the recording.",
        observed={"streams": affected},
    )


@rule("SENSOR_DUPLICATE_FRAMES")
def duplicate_frames(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    affected = _issue_streams(ctx, "DUPLICATE_FRAMES") + _issue_streams(ctx, "DUPLICATE_SAMPLES")
    if not affected:
        return ok(definition, "No stream reports duplicate sample timestamps.")
    return fail(
        definition,
        "Duplicate samples detected on: " + ", ".join(affected) + ".",
        correction="Duplicate timestamps usually indicate an export fault; request a re-export.",
        observed={"streams": affected},
    )


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------
@rule("SYNC_TIMESTAMP_MONOTONIC")
def timestamps_monotonic(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    affected = _issue_streams(ctx, "NON_MONOTONIC_TIMESTAMPS")
    if not affected:
        return ok(definition, "All stream timestamps are non-decreasing.")
    return fail(
        definition,
        "Non-monotonic timestamps on: " + ", ".join(affected) + ".",
        correction=(
            "Samples arrive out of order. Every derived timestamp on these streams must be treated "
            "as unreliable until the export is corrected."
        ),
        observed={"streams": affected},
    )


@rule("SYNC_CAMERA_OFFSET")
def camera_offset(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    limit = float(get_settings().section("synchronization").get("max_camera_offset_ms", 50.0))
    observed = ctx.sync.max_camera_offset_ms
    if observed is None:
        return skip(definition, "No camera stream offset could be measured against the master clock.")
    if abs(observed) <= limit:
        return ok(
            definition,
            f"Largest camera-to-master offset is {observed:.1f} ms (budget {limit:.0f} ms).",
            observed={"max_offset_ms": observed, "limit_ms": limit},
        )
    return fail(
        definition,
        f"Largest camera-to-master offset is {observed:.1f} ms, exceeding the {limit:.0f} ms budget.",
        correction="Camera desynchronisation shifts every frame-based evidence timestamp; verify the rig clock.",
        observed={"max_offset_ms": observed, "limit_ms": limit},
    )


@rule("SYNC_TELEMETRY_OFFSET")
def telemetry_offset(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    limit = float(get_settings().section("synchronization").get("max_telemetry_offset_ms", 100.0))
    observed = ctx.sync.max_telemetry_offset_ms
    if observed is None:
        return skip(definition, "No telemetry stream offset could be measured against the master clock.")
    if abs(observed) <= limit:
        return ok(
            definition,
            f"Largest telemetry-to-master offset is {observed:.1f} ms (budget {limit:.0f} ms).",
            observed={"max_offset_ms": observed, "limit_ms": limit},
        )
    return fail(
        definition,
        f"Largest telemetry-to-master offset is {observed:.1f} ms, exceeding the {limit:.0f} ms budget.",
        correction="Verify the telemetry clock source; speed-based observations inherit this offset.",
        observed={"max_offset_ms": observed, "limit_ms": limit},
    )


@rule("SYNC_TIMESTAMP_GAP")
def timestamp_gap(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    affected = [h for h in ctx.sync.stream_health if any("GAP" in issue for issue in h.issues)]
    if not affected:
        return ok(definition, "No stream contains an oversized sample gap.")
    return fail(
        definition,
        "Sample gaps detected on: "
        + ", ".join(f"{h.key} (max {h.max_gap_ms:.0f} ms)" for h in affected if h.max_gap_ms is not None)
        + ".",
        correction="Data is missing for part of the clip; check evidence coverage around the gap.",
        observed={"streams": [h.key for h in affected]},
    )


@rule("SYNC_DUPLICATE_TIMESTAMPS")
def duplicate_timestamps(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    affected = _issue_streams(ctx, "DUPLICATE_SAMPLES") + _issue_streams(ctx, "DUPLICATE_FRAMES")
    if not affected:
        return ok(definition, "No duplicate sample timestamps found.")
    return fail(
        definition,
        "Duplicate sample timestamps on: " + ", ".join(sorted(set(affected))) + ".",
        correction="Request a corrected export; duplicated samples distort rate and availability metrics.",
        observed={"streams": sorted(set(affected))},
    )


# ---------------------------------------------------------------------------
# Data completeness and metadata
# ---------------------------------------------------------------------------
@rule("DATA_MANDATORY_FIELDS")
def mandatory_fields(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    required = get_config_store().required_canonical_fields()
    missing = []
    for name in required:
        value = getattr(ctx.metadata, name, None)
        if value in (None, "", [], {}):
            missing.append(name)
    if not missing:
        return ok(
            definition,
            f"All {len(required)} mandatory metadata field(s) are populated.",
            observed={"required": required},
        )
    return fail(
        definition,
        "Mandatory metadata field(s) missing: " + ", ".join(missing) + ".",
        correction=(
            "Confirm the field mapping on the Connections page - the source may expose these under "
            "different names - or request a complete export."
        ),
        observed={"missing": missing, "required": required},
    )


@rule("DATA_COUNTRY_AUTHORITATIVE")
def country_authoritative(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    metadata = ctx.metadata
    source_field = metadata.country_source_field

    if not metadata.country_code:
        return fail(
            definition,
            "No country_code is present in the event's authoritative metadata.",
            correction=(
                "Map the source's country code field on the Connections page. Country must never be "
                "inferred from a filename or path."
            ),
            observed={"country": metadata.country, "country_source_field": source_field},
        )

    if source_field and not is_authoritative_country_source(source_field):
        return fail(
            definition,
            f"The country was resolved from '{source_field}', which is not an authoritative metadata "
            "field (filename- and path-derived values are not accepted).",
            correction="Map an authoritative country field, or exclude this event from the run.",
            observed={"country_code": metadata.country_code, "country_source_field": source_field},
        )

    if ctx.query_country_code and metadata.country_code.upper() != ctx.query_country_code.upper():
        return fail(
            definition,
            f"Event country_code '{metadata.country_code}' does not match the run filter "
            f"'{ctx.query_country_code}'.",
            correction="The source returned an out-of-scope event; verify the native query translation.",
            observed={"event": metadata.country_code, "filter": ctx.query_country_code},
        )

    return ok(
        definition,
        f"Country resolved to {metadata.country_code} from authoritative field "
        f"'{source_field or 'country_code'}'.",
        observed={"country_code": metadata.country_code, "source_field": source_field},
    )


@rule("DATA_LOCALIZATION_QUALITY")
def localization_quality(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    quality = ctx.trajectory.localization_quality
    if quality is None:
        return skip(definition, "The source did not report a localisation quality channel.")
    # 0.7 is the platform's own usability floor for distance-based timestamping,
    # not a project safety threshold.
    floor = 0.7
    if quality >= floor:
        return ok(
            definition,
            f"Mean localisation quality {quality:.2f} is at or above the {floor:.2f} usability floor.",
            observed={"localization_quality": quality, "floor": floor},
        )
    return fail(
        definition,
        f"Mean localisation quality {quality:.2f} is below the {floor:.2f} usability floor. "
        "Distance-based timestamps derived from this trajectory carry elevated uncertainty.",
        correction="Review GPS/localisation coverage for this clip before accepting derived timestamps.",
        observed={"localization_quality": quality, "floor": floor},
    )


# ---------------------------------------------------------------------------
# Traffic control
# ---------------------------------------------------------------------------
SIGNALISED_CONTROLS = {"traffic_light", "temporary_signal", "railroad_control"}


@rule("TRAFFIC_CONTROL_PRESENT")
def traffic_control_present(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    declared = {str(c).lower() for c in ctx.metadata.traffic_control_entity}
    if not declared or declared <= {"uncontrolled", "unknown"}:
        return skip(definition, "The event does not declare a signalised traffic control entity.")
    if not declared & SIGNALISED_CONTROLS:
        return skip(definition, "The declared traffic control is not signal-based.")

    mapped = ctx.bundle.map_context.by_type("traffic_signal") if ctx.bundle.map_context.available else []
    detected = [d for d in ctx.bundle.detections if d.object_type == "traffic_light"]
    if mapped or detected:
        return ok(
            definition,
            f"Traffic control context present: {len(mapped)} mapped signal(s), "
            f"{len(detected)} signal detection(s).",
            observed={"mapped_signals": len(mapped), "detections": len(detected)},
        )
    return fail(
        definition,
        "The event declares a signalised junction, but neither the map nor the detections contain "
        "any traffic signal.",
        correction="Verify the junction selection and the map version; the declared control may be wrong.",
        observed={"declared": sorted(declared)},
    )


#: Legal signal transitions. Region-configurable: Germany and Austria include
#: the red-yellow phase, which most other regions do not.
REGION_SIGNAL_MODEL: dict[str, dict[str, set[str]]] = {
    "default": {
        "red": {"red", "green", "flashing_red", "off", "occluded", "unknown"},
        "green": {"green", "yellow", "flashing_green", "off", "occluded", "unknown"},
        "yellow": {"yellow", "red", "off", "occluded", "unknown"},
        "flashing_red": {"flashing_red", "red", "green", "off", "occluded", "unknown"},
        "flashing_yellow": {"flashing_yellow", "yellow", "red", "green", "off", "occluded", "unknown"},
        "flashing_green": {"flashing_green", "green", "yellow", "off", "occluded", "unknown"},
        "off": {"off", "red", "green", "yellow", "flashing_yellow", "occluded", "unknown"},
        "occluded": set(),  # occlusion may be followed by anything
        "unknown": set(),
    },
    "DE": {
        "red": {"red", "red_yellow", "flashing_red", "off", "occluded", "unknown"},
        "red_yellow": {"red_yellow", "green", "off", "occluded", "unknown"},
        "green": {"green", "yellow", "off", "occluded", "unknown"},
        "yellow": {"yellow", "red", "off", "occluded", "unknown"},
        "flashing_red": {"flashing_red", "red", "off", "occluded", "unknown"},
        "flashing_yellow": {"flashing_yellow", "red", "green", "yellow", "off", "occluded", "unknown"},
        "off": {"off", "red", "green", "yellow", "red_yellow", "flashing_yellow", "occluded", "unknown"},
        "occluded": set(),
        "unknown": set(),
    },
}
REGION_SIGNAL_MODEL["AT"] = REGION_SIGNAL_MODEL["DE"]


@rule("TRAFFIC_LIGHT_STATE_CONSISTENCY")
def traffic_light_state_consistency(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    observations = ctx.scene.traffic_light_observations
    if len(observations) < 2:
        return skip(definition, "Fewer than two distinct signal-state observations were made.")

    country = (ctx.metadata.country_code or "").upper()
    model = REGION_SIGNAL_MODEL.get(country, REGION_SIGNAL_MODEL["default"])

    illegal: list[str] = []
    for before, after in zip(observations, observations[1:], strict=False):
        allowed = model.get(before["state"])
        if not allowed:
            continue  # unknown / occluded: no constraint
        if after["state"] not in allowed:
            illegal.append(
                f"{before['state']} -> {after['state']} at t={after['t_start']:.2f}s"
            )

    if not illegal:
        return ok(
            definition,
            f"Signal transitions follow the {country or 'default'} signal model "
            f"({len(observations)} observed state(s)).",
            observed={"states": [o["state"] for o in observations], "model": country or "default"},
        )
    return fail(
        definition,
        f"Signal transitions not legal under the {country or 'default'} signal model: "
        + "; ".join(illegal)
        + ".",
        correction=(
            "Either the signal-state classification is wrong, or the observed signal is not the one "
            "governing the ego lane. Review the signal close-up evidence."
        ),
        observed={"illegal_transitions": illegal, "model": country or "default"},
    )
