"""Field-level confidence.

Every field carries its own confidence, computed from the evidence components
that were actually available for it. Two rules matter more than the arithmetic:

1. **A missing component never counts as agreement.** Weights are renormalised
   over the components present, and the missing ones are named in the
   explanation. A field with only a model score does not get to look as
   trustworthy as one corroborated by map, cameras and time.

2. **The overall number is minimum-biased.** A record is only as trustworthy as
   its weakest populated field, so a single weak field cannot be averaged away
   by strong neighbours.

Every value comes back with a :class:`ConfidenceExplanation` so the dashboard
can answer "why was this confidence generated?" with the actual arithmetic.
"""

from __future__ import annotations

from backend.configstore import get_config_store
from backend.models.contracts import ConfidenceBand, ConfidenceExplanation

COMPONENT_LABELS = {
    "model_confidence": "Model confidence",
    "cross_camera_agreement": "Cross-camera agreement",
    "map_agreement": "Map agreement",
    "temporal_stability": "Temporal stability",
    "sensor_quality": "Sensor quality",
}


def component_weights() -> dict[str, float]:
    return {name: float(spec.get("weight", 0.0)) for name, spec in get_config_store().confidence_components().items()}


def compute_field_confidence(
    field_name: str,
    components: dict[str, float | None],
) -> tuple[float, ConfidenceExplanation]:
    """Combine evidence components into one field confidence.

    ``components`` maps component name -> value in 0..1, or ``None`` when that
    evidence was not available for this field.
    """
    weights = component_weights()
    present = {
        name: max(0.0, min(1.0, float(value)))
        for name, value in components.items()
        if value is not None and name in weights
    }
    missing = sorted(set(weights) - set(present))

    if not present:
        return 0.0, ConfidenceExplanation(
            components={},
            weights={},
            missing_components=missing,
            final=0.0,
            narrative=(
                f"No confidence evidence was available for '{field_name}', so it is reported as 0.0 "
                "and left for manual entry."
            ),
        )

    total_weight = sum(weights[name] for name in present)
    used_weights = {name: round(weights[name] / total_weight, 4) for name in present}
    final = sum(present[name] * used_weights[name] for name in present)
    final = round(max(0.0, min(1.0, final)), 4)

    lines = [
        f"{COMPONENT_LABELS.get(name, name)}: {present[name]:.2f} (weight {used_weights[name]:.2f})"
        for name in sorted(present)
    ]
    if missing:
        lines.append(
            "Not available: "
            + ", ".join(COMPONENT_LABELS.get(name, name) for name in missing)
            + " - weights were renormalised over the remaining evidence."
        )
    lines.append(f"Final confidence: {final:.2f}")

    return final, ConfidenceExplanation(
        components={name: round(value, 4) for name, value in present.items()},
        weights=used_weights,
        missing_components=missing,
        final=final,
        narrative="\n".join(lines),
    )


def band_for(confidence: float) -> ConfidenceBand:
    """Map a confidence onto its routing band."""
    bands = get_config_store().confidence_bands()
    for name in ("auto_confirm", "verify", "suggest", "manual"):
        spec = bands.get(name)
        if not spec:
            continue
        low = float(spec.get("min", 0.0))
        high = float(spec.get("max", 1.01))
        if low <= confidence < high:
            return ConfidenceBand(name)
    return ConfidenceBand.MANUAL


def band_policy(band: ConfidenceBand) -> dict[str, object]:
    spec = get_config_store().confidence_bands().get(band.value, {})
    return {
        "action": spec.get("action", "leave_blank"),
        "reviewer": spec.get("reviewer", "manual_entry"),
        "auto_select": bool(spec.get("auto_select", False)),
        "min": spec.get("min"),
        "max": spec.get("max"),
    }


def may_auto_select(confidence: float) -> bool:
    """Whether a value at this confidence may be pre-selected for the reviewer.

    The band decides, but the configured hard floor always wins - no band can
    auto-select below it.
    """
    if confidence < get_config_store().confidence_hard_floor():
        return False
    return bool(band_policy(band_for(confidence))["auto_select"])


def is_safety_critical(field_name: str) -> bool:
    return field_name in get_config_store().safety_critical_fields()


def aggregate_overall(field_confidences: dict[str, float]) -> float:
    """Minimum-biased aggregate over the populated field confidences.

    ``0.6 * weakest + 0.4 * mean``: the weakest field dominates, but a record
    with many strong fields still scores above one with only the weak field.
    """
    values = [v for v in field_confidences.values() if v is not None]
    if not values:
        return 0.0
    weakest = min(values)
    mean = sum(values) / len(values)
    return round(max(0.0, min(1.0, 0.6 * weakest + 0.4 * mean)), 4)


def describe_policy() -> dict[str, object]:
    """The routing policy, for the Validation Rules / Administration pages."""
    store = get_config_store()
    return {
        "bands": store.confidence_bands(),
        "fields": store.confidence_fields(),
        "components": store.confidence_components(),
        "hard_floor": store.confidence_hard_floor(),
        "safety_critical_fields": sorted(store.safety_critical_fields()),
        "overall_aggregation": store.confidence.get("overall_aggregation", "weighted_min_biased"),
    }
