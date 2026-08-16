"""Redaction applied before evidence leaves the approved environment.

Three layers:

* **Structural** - internal identifiers are replaced by salted pseudonyms and
  precise coordinates are reduced to the configured precision.
* **Pattern** - every exported text value is scanned for sensitive patterns
  (credentials, tokens, e-mail addresses, host paths, plates, VINs) and hits are
  masked and reported.
* **Image** - configured rectangular regions are burned out of exported raster
  evidence.

The policy is **fail-closed**: if redaction is required but cannot be applied,
the export is refused rather than shipped unredacted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.configstore import get_config_store
from backend.identity import pseudonymize

MASK = "[REDACTED]"


@dataclass
class RedactionHit:
    field_path: str
    pattern_name: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field_path, "pattern": self.pattern_name, "count": self.count}


@dataclass
class RedactionReport:
    applied: bool = True
    hits: list[RedactionHit] = field(default_factory=list)
    pseudonymised_fields: list[str] = field(default_factory=list)
    rounded_fields: list[str] = field(default_factory=list)
    image_regions: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "clean": self.clean,
            "hits": [h.to_dict() for h in self.hits],
            "pseudonymised_fields": sorted(set(self.pseudonymised_fields)),
            "rounded_fields": sorted(set(self.rounded_fields)),
            "image_regions": sorted(set(self.image_regions)),
            "failures": self.failures,
        }


class RedactionError(Exception):
    """Raised when redaction is required and cannot be applied (fail-closed)."""


class RedactionPolicy:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = config if config is not None else get_config_store().redaction
        self.enabled = bool(raw.get("enabled", True))
        self.fail_closed = bool(raw.get("fail_closed", True))
        self.image_regions: list[dict[str, Any]] = list(raw.get("image_regions", []) or [])

        text_rules = raw.get("text_rules", {}) or {}
        coordinate = text_rules.get("coordinate_precision", {}) or {}
        self.round_coordinates = bool(coordinate.get("enabled", True))
        self.coordinate_decimals = int(coordinate.get("decimals", 2))
        self.coordinate_fields = {str(f).lower() for f in coordinate.get("fields", [])}
        self.pseudonymise_fields = {str(f).lower() for f in text_rules.get("pseudonymise_fields", [])}

        self.patterns: list[tuple[str, re.Pattern[str]]] = []
        for spec in text_rules.get("sensitive_patterns", []) or []:
            try:
                self.patterns.append((str(spec["name"]), re.compile(str(spec["pattern"]))))
            except (KeyError, re.error) as exc:
                raise RedactionError(f"Invalid redaction pattern {spec!r}: {exc}") from exc

    # -- text ------------------------------------------------------------
    def redact_text(self, value: str, path: str, report: RedactionReport) -> str:
        redacted = value
        for name, pattern in self.patterns:
            redacted, count = pattern.subn(MASK, redacted)
            if count:
                report.hits.append(RedactionHit(field_path=path, pattern_name=name, count=count))
        return redacted

    # -- structured ------------------------------------------------------
    def redact_value(self, key: str, value: Any, path: str, report: RedactionReport) -> Any:
        lowered = key.lower()

        if lowered in self.pseudonymise_fields and value not in (None, ""):
            report.pseudonymised_fields.append(path)
            return pseudonymize(str(value), prefix=lowered[:3].upper())

        if self.round_coordinates and lowered in self.coordinate_fields:
            try:
                report.rounded_fields.append(path)
                return round(float(value), self.coordinate_decimals)
            except (TypeError, ValueError):
                return value

        if isinstance(value, str):
            return self.redact_text(value, path, report)
        return value

    def redact_mapping(self, data: Any, report: RedactionReport | None = None, path: str = "") -> tuple[Any, RedactionReport]:
        """Recursively redact a JSON-shaped structure."""
        active = report or RedactionReport(applied=self.enabled)
        if not self.enabled:
            active.applied = False
            return data, active

        if isinstance(data, dict):
            out: dict[str, Any] = {}
            for key, value in data.items():
                child_path = f"{path}.{key}" if path else str(key)
                if isinstance(value, (dict, list)):
                    out[key], _ = self.redact_mapping(value, active, child_path)
                else:
                    out[key] = self.redact_value(str(key), value, child_path, active)
            return out, active

        if isinstance(data, list):
            return [self.redact_mapping(item, active, f"{path}[{i}]")[0] for i, item in enumerate(data)], active

        if isinstance(data, str):
            return self.redact_text(data, path or "value", active), active

        return data, active

    # -- images ----------------------------------------------------------
    def redact_image(self, source: Path, destination: Path, kind: str = "screenshot") -> RedactionReport:
        """Burn configured regions out of a raster image.

        Requires an image library. When redaction is required and no library is
        available, the report records a failure and the caller must refuse to
        export the image.
        """
        report = RedactionReport(applied=self.enabled)
        if not self.enabled:
            report.applied = False
            return report

        regions = [r for r in self.image_regions if kind in (r.get("applies_to") or [kind])]
        if not regions:
            return report

        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # noqa: F401
        except Exception:
            report.failures.append(
                "No image library is available to apply redaction. Install the 'vision' extra "
                "(opencv-python-headless) or export this evidence from inside the approved environment."
            )
            return report

        image = cv2.imread(str(source))
        if image is None:
            report.failures.append(f"Could not read image {source.name} to redact it.")
            return report

        height, width = image.shape[:2]
        for region in regions:
            x = int(float(region.get("x", 0.0)) * width)
            y = int(float(region.get("y", 0.0)) * height)
            w = int(float(region.get("w", 0.0)) * width)
            h = int(float(region.get("h", 0.0)) * height)
            cv2.rectangle(image, (x, y), (x + w, y + h), (0, 0, 0), thickness=-1)
            report.image_regions.append(str(region.get("name", "region")))

        destination.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(destination), image):
            report.failures.append(f"Could not write the redacted image to {destination}.")
        return report

    # -- preview ----------------------------------------------------------
    def preview(self, data: Any) -> dict[str, Any]:
        """What redaction WOULD do, without writing anything."""
        redacted, report = self.redact_mapping(data)
        return {
            "enabled": self.enabled,
            "fail_closed": self.fail_closed,
            "report": report.to_dict(),
            "redacted_sample": redacted,
            "image_regions": [
                {"name": r.get("name"), "applies_to": r.get("applies_to"), "reason": r.get("reason")}
                for r in self.image_regions
            ],
        }

    def enforce(self, report: RedactionReport) -> None:
        if self.enabled and self.fail_closed and report.failures:
            raise RedactionError(
                "Redaction is required but could not be applied: " + "; ".join(report.failures)
            )


_policy: RedactionPolicy | None = None


def get_redaction_policy(refresh: bool = False) -> RedactionPolicy:
    global _policy
    if _policy is None or refresh:
        _policy = RedactionPolicy()
    return _policy
