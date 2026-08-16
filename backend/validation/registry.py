"""Validation rule registry, execution context and result helpers.

Rule implementations live in the ``rules_*`` modules and register themselves
with :func:`rule`. Keeping the registry in its own module avoids a circular
import between the engine and the rules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.configstore import RuleDefinition
from backend.models.contracts import (
    BehaviorAnalysis,
    EventBundle,
    EventMetadata,
    GeometryResult,
    SceneAnalysis,
    SensorConfiguration,
    Severity,
    SynchronizationReport,
    TimestampMarker,
    Trajectory,
    ValidationOutcome,
)

RuleFunc = Callable[["ValidationContext", RuleDefinition], "ValidationOutcome | list[ValidationOutcome] | None"]

_REGISTRY: dict[str, RuleFunc] = {}


def rule(rule_id: str) -> Callable[[RuleFunc], RuleFunc]:
    """Register a rule implementation under its catalogue id."""

    def decorator(func: RuleFunc) -> RuleFunc:
        if rule_id in _REGISTRY:
            raise RuntimeError(f"Duplicate validation rule implementation for '{rule_id}'")
        _REGISTRY[rule_id] = func
        return func

    return decorator


def registry() -> dict[str, RuleFunc]:
    return dict(_REGISTRY)


def implemented_rule_ids() -> set[str]:
    return set(_REGISTRY)


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------
@dataclass
class ValidationContext:
    """Everything the rules may inspect for one event."""

    metadata: EventMetadata
    bundle: EventBundle
    sensor_config: SensorConfiguration
    sync: SynchronizationReport
    trajectory: Trajectory
    geometry: GeometryResult
    scene: SceneAnalysis
    behavior: BehaviorAnalysis
    canonical_event_key: str = ""
    duplicate_exists: bool = False
    origin: datetime | None = None
    query_country_code: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    # -- convenience ------------------------------------------------------
    def marker(self, name: str) -> TimestampMarker | None:
        return self.geometry.marker(name)

    def marker_t(self, name: str) -> float | None:
        found = self.marker(name)
        return found.t if (found is not None and found.available) else None

    @property
    def source_start_t(self) -> float:
        return self.bundle.source_start_t

    @property
    def source_end_t(self) -> float | None:
        return self.bundle.source_end_t

    def all_marker_times(self) -> dict[str, float]:
        return {
            m.name: m.t
            for m in self.geometry.markers
            if m.available and m.t is not None
        }


# ---------------------------------------------------------------------------
# Outcome helpers
# ---------------------------------------------------------------------------
def _base(definition: RuleDefinition, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rule_id": definition.id,
        "category": definition.category,
        "severity": Severity(definition.severity),
        "blocks_processing": definition.blocks_processing,
        "blocks_export": definition.blocks_export,
        "requires_review": definition.requires_review,
        "rule_version": definition.version,
    }
    payload.update(overrides)
    return payload


def ok(definition: RuleDefinition, message: str = "", observed: dict[str, Any] | None = None) -> ValidationOutcome:
    return ValidationOutcome(
        **_base(
            definition,
            passed=True,
            message=message or f"{definition.id} passed.",
            observed=observed or {},
            blocks_processing=False,
            blocks_export=False,
            requires_review=False,
        )
    )


def fail(
    definition: RuleDefinition,
    message: str,
    *,
    correction: str | None = None,
    observed: dict[str, Any] | None = None,
    severity: Severity | None = None,
) -> ValidationOutcome:
    payload = _base(definition, passed=False, message=message, observed=observed or {})
    payload["recommended_correction"] = correction
    if severity is not None:
        payload["severity"] = severity
    return ValidationOutcome(**payload)


def skip(definition: RuleDefinition, reason: str) -> ValidationOutcome:
    """A rule that could not be evaluated.

    Skipped is deliberately distinct from passed: "we could not check this" and
    "we checked and it is fine" must never look the same on a QA report.
    """
    return ValidationOutcome(
        **_base(
            definition,
            passed=True,
            skipped=True,
            skip_reason=reason,
            message=f"{definition.id} was not evaluated: {reason}",
            blocks_processing=False,
            blocks_export=False,
            requires_review=False,
        )
    )
