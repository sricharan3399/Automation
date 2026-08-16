"""Evidence generation.

Produces the configured capture points for one event. Two categories:

* **Derivable now** - the map/trajectory diagram, the telemetry summary, the
  validation warnings and the final review snapshot. These are generated from
  data the platform already holds, redacted, hashed and written to disk.

* **Frame-dependent** - first-visible, full-view, signal close-up and the
  junction crossings need actual camera pixels. Stream manifests carry
  references, not images, so unless an approved frame provider is configured
  these items are recorded as ``available=False`` with the reason. They are
  never silently omitted, because a manifest that quietly lacks a capture point
  reads like evidence that was reviewed and found unremarkable.

Every written artefact is hashed so it can be shown to be unmodified.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.evidence.redaction import RedactionPolicy, get_redaction_policy
from backend.evidence.svg_map import render_map_svg
from backend.identity import content_hash
from backend.models.contracts import EvidenceItem, ReviewPackage
from backend.settings import get_settings
from backend.validation.registry import ValidationContext

log = logging.getLogger(__name__)

#: Capture points that require camera pixels.
FRAME_DEPENDENT = {
    "first_visible",
    "full_view",
    "signal_close_up",
    "before_junction_entry",
    "wait_line_crossing",
    "junction_entry",
    "junction_exit",
}

#: Capture points this build derives without pixels.
DERIVED = {
    "map_trajectory",
    "telemetry_summary",
    "validation_warnings",
    "final_review",
}

FrameProvider = Callable[[str, float, str], bytes | None]


class EvidenceGenerator:
    """Writes evidence for one event into ``<run_dir>/evidence/<event_ref>/``."""

    def __init__(
        self,
        run_dir: Path,
        policy: RedactionPolicy | None = None,
        frame_provider: FrameProvider | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.policy = policy or get_redaction_policy()
        self.frame_provider = frame_provider
        self.config = get_settings().section("evidence")

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def capture_points(self) -> list[str]:
        return [str(p) for p in self.config.get("capture_points", [])]

    # -- helpers ---------------------------------------------------------
    def _event_dir(self, event_ref: str) -> Path:
        path = self.run_dir / "evidence" / event_ref
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write(self, path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return content_hash(data)

    def _write_json(self, path: Path, payload: Any) -> tuple[str, dict[str, Any]]:
        redacted, report = self.policy.redact_mapping(payload)
        self.policy.enforce(report)
        data = json.dumps(redacted, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        return self._write(path, data), report.to_dict()

    # -- generation ------------------------------------------------------
    def generate(self, ctx: ValidationContext, package: ReviewPackage) -> list[EvidenceItem]:
        if not self.enabled:
            return [
                EvidenceItem(
                    evidence_id="evidence_disabled",
                    purpose="configuration",
                    kind="json",
                    available=False,
                    unavailable_reason="Evidence generation is disabled in the active configuration.",
                )
            ]

        event_ref = package.anonymized_event_ref
        directory = self._event_dir(event_ref)
        relative_root = Path("evidence") / event_ref
        items: list[EvidenceItem] = []

        for point in self.capture_points():
            if point in DERIVED:
                items.append(self._derive(point, ctx, package, directory, relative_root))
            elif point in FRAME_DEPENDENT:
                items.append(self._frame(point, ctx, package, directory, relative_root))
            else:
                items.append(
                    EvidenceItem(
                        evidence_id=f"{event_ref}:{point}",
                        purpose=point,
                        kind="json",
                        available=False,
                        unavailable_reason=f"Capture point '{point}' is not implemented in this build.",
                    )
                )
        return items

    # -- derived artefacts -------------------------------------------------
    def _derive(
        self,
        point: str,
        ctx: ValidationContext,
        package: ReviewPackage,
        directory: Path,
        relative_root: Path,
    ) -> EvidenceItem:
        evidence_id = f"{package.anonymized_event_ref}:{point}"
        try:
            if point == "map_trajectory":
                svg = render_map_svg(
                    ctx.trajectory,
                    ctx.geometry,
                    ctx.bundle.map_context,
                    title=f"{package.anonymized_event_ref} - trajectory and junction geometry",
                )
                path = directory / "map_trajectory.svg"
                digest = self._write(path, svg.encode("utf-8"))
                return EvidenceItem(
                    evidence_id=evidence_id,
                    purpose=point,
                    kind="svg",
                    relative_path=str(relative_root / path.name),
                    content_hash=digest,
                    redacted=True,
                    redaction_report={
                        "note": "Rendered in the local metric frame; no global coordinates are included."
                    },
                )

            if point == "telemetry_summary":
                payload: dict[str, Any] = {
                    "event_ref": package.anonymized_event_ref,
                    "master_stream": ctx.sync.master_stream,
                    "clip_start_t": ctx.sync.master_start_t,
                    "clip_end_t": ctx.sync.master_end_t,
                    "synchronization_quality": ctx.sync.quality,
                    "synchronization_confidence": ctx.sync.confidence,
                    "streams": [
                        {
                            "stream": h.key,
                            "requirement": h.requirement.value,
                            "present": h.present,
                            "availability_pct": h.availability_pct,
                            "sync_offset_ms": h.sync_offset_ms,
                            "max_gap_ms": h.max_gap_ms,
                            "quality_score": h.quality_score,
                            "status": h.status,
                            "issues": h.issues,
                        }
                        for h in ctx.sync.stream_health
                    ],
                    "trajectory": {
                        "valid": ctx.trajectory.valid,
                        "invalid_reason": ctx.trajectory.invalid_reason,
                        "length_m": ctx.trajectory.total_length_m,
                        "duration_s": ctx.trajectory.duration_s,
                        "localization_quality": ctx.trajectory.localization_quality,
                    },
                    "behavior": {
                        "available": ctx.behavior.available,
                        "stop_classification": ctx.behavior.stop_classification,
                        "minimum_speed_mps": ctx.behavior.minimum_speed_mps,
                        "stop_duration_s": ctx.behavior.stop_duration_s,
                        "maneuver": ctx.behavior.maneuver,
                        "observations": [
                            {"name": o.name, "observation": o.observation, "value": o.value, "unit": o.unit}
                            for o in ctx.behavior.observations
                        ],
                        "note": "Observations only. Interpretation is a human decision.",
                    },
                }
                path = directory / "telemetry_summary.json"
                digest, report = self._write_json(path, payload)
                return EvidenceItem(
                    evidence_id=evidence_id,
                    purpose=point,
                    kind="json",
                    relative_path=str(relative_root / path.name),
                    content_hash=digest,
                    redacted=True,
                    redaction_report=report,
                )

            if point == "validation_warnings":
                payload = {
                    "event_ref": package.anonymized_event_ref,
                    "rule_version": package.rule_version,
                    "counts": package.validation.counts(),
                    "failures": [
                        {
                            "rule_id": o.rule_id,
                            "category": o.category,
                            "severity": o.severity.value,
                            "message": o.message,
                            "recommended_correction": o.recommended_correction,
                            "blocks_export": o.blocks_export,
                            "observed": o.observed,
                        }
                        for o in package.validation.failures
                    ],
                    "skipped": [
                        {"rule_id": o.rule_id, "reason": o.skip_reason}
                        for o in package.validation.outcomes
                        if o.skipped
                    ],
                }
                path = directory / "validation_warnings.json"
                digest, report = self._write_json(path, payload)
                return EvidenceItem(
                    evidence_id=evidence_id,
                    purpose=point,
                    kind="json",
                    relative_path=str(relative_root / path.name),
                    content_hash=digest,
                    redacted=True,
                    redaction_report=report,
                )

            # final_review
            payload = {
                "event_ref": package.anonymized_event_ref,
                "canonical_event_key": package.canonical_event_key,
                "status": package.status.value,
                "overall_confidence": package.overall_confidence,
                "automation_recommendation": package.automation_recommendation,
                "abnormality_categories": package.abnormality_categories,
                "blocking_error_count": package.blocking_error_count,
                "recommendations": [
                    {
                        "field": r.field_name,
                        "recommended_value": r.recommended_value,
                        "confidence": r.confidence,
                        "band": r.band.value,
                        "auto_selected": r.auto_selected,
                        "status": r.status.value,
                        "reason": r.reason,
                    }
                    for r in package.recommendations
                ],
                "versions": {
                    "software": package.software_version,
                    "rules": package.rule_version,
                    "model": package.model_version,
                    "map": package.map_version,
                    "contract": package.contract_version,
                },
            }
            path = directory / "final_review.json"
            digest, report = self._write_json(path, payload)
            return EvidenceItem(
                evidence_id=evidence_id,
                purpose=point,
                kind="json",
                relative_path=str(relative_root / path.name),
                content_hash=digest,
                redacted=True,
                redaction_report=report,
            )

        except Exception as exc:
            log.exception("Evidence generation failed for %s / %s", package.anonymized_event_ref, point)
            return EvidenceItem(
                evidence_id=evidence_id,
                purpose=point,
                kind="json",
                available=False,
                unavailable_reason=f"Evidence generation failed: {exc}",
            )

    # -- frame-dependent artefacts -------------------------------------------
    def _frame(
        self,
        point: str,
        ctx: ValidationContext,
        package: ReviewPackage,
        directory: Path,
        relative_root: Path,
    ) -> EvidenceItem:
        evidence_id = f"{package.anonymized_event_ref}:{point}"
        t = self._time_for(point, ctx)

        if self.frame_provider is None:
            return EvidenceItem(
                evidence_id=evidence_id,
                purpose=point,
                kind="image",
                t_rel_s=t,
                available=False,
                unavailable_reason=(
                    "No approved camera-frame provider is configured, so this frame could not be "
                    "captured. Stream manifests reference frames but do not carry pixels. "
                    "Configure an approved frame source on the Connections page to enable image evidence."
                ),
            )

        if t is None:
            return EvidenceItem(
                evidence_id=evidence_id,
                purpose=point,
                kind="image",
                available=False,
                unavailable_reason=f"The instant for '{point}' could not be derived for this event.",
            )

        camera = "front_main"
        payload = self.frame_provider(package.anonymized_event_ref, t, camera)
        if payload is None:
            return EvidenceItem(
                evidence_id=evidence_id,
                purpose=point,
                kind="image",
                camera=camera,
                t_rel_s=t,
                available=False,
                unavailable_reason=f"The frame provider returned no frame at t={t:.3f}s on {camera}.",
            )

        suffix = str(self.config.get("image_format", "png"))
        path = directory / f"{point}.{suffix}"
        self._write(path, payload)
        report = self.policy.redact_image(path, path, kind="camera_frame")
        self.policy.enforce(report)
        # Hashed after redaction: the manifest must attest to what was exported,
        # not to the raw frame that never leaves the approved environment.
        return EvidenceItem(
            evidence_id=evidence_id,
            purpose=point,
            kind="image",
            camera=camera,
            t_rel_s=t,
            relative_path=str(relative_root / path.name),
            content_hash=content_hash(path.read_bytes()),
            redacted=report.applied,
            redaction_report=report.to_dict(),
        )

    @staticmethod
    def _time_for(point: str, ctx: ValidationContext) -> float | None:
        if point == "first_visible":
            return ctx.scene.first_visible_t
        if point == "full_view":
            return ctx.scene.full_view_t
        if point == "signal_close_up":
            observations = ctx.scene.traffic_light_observations
            return float(observations[0]["t_start"]) if observations else None
        if point == "before_junction_entry":
            entry = ctx.marker_t("junction_entry")
            return max(0.0, entry - 2.0) if entry is not None else None
        if point == "wait_line_crossing":
            return ctx.marker_t("wait_line_crossing")
        if point == "junction_entry":
            return ctx.marker_t("junction_entry")
        if point == "junction_exit":
            return ctx.marker_t("junction_exit")
        return None
