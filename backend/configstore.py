"""Loader for the YAML configuration artefacts under ``config/``.

Separate from :mod:`backend.settings` because these files describe *what the
platform knows* (taxonomy, rules, thresholds, CSV templates) rather than *how
the process is wired* (ports, paths, credentials).

Everything is cached and hot-reloadable so the Administration page can apply a
change without restarting the service.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from backend.settings import CONFIG_DIR

TAXONOMY_PATH = CONFIG_DIR / "taxonomy.yaml"
FIELD_MAPPING_PATH = CONFIG_DIR / "field_mapping.yaml"
VALIDATION_RULES_PATH = CONFIG_DIR / "validation_rules.yaml"
CONFIDENCE_PATH = CONFIG_DIR / "confidence_thresholds.yaml"
REDACTION_PATH = CONFIG_DIR / "redaction_regions.yaml"
CSV_TEMPLATE_DIR = CONFIG_DIR / "csv_templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True)
class RuleDefinition:
    """One entry from ``config/validation_rules.yaml``."""

    id: str
    category: str
    description: str
    enabled: bool
    severity: str
    blocks_processing: bool
    blocks_export: bool
    requires_review: bool
    threshold: float | int | None
    threshold_source: str
    version: str
    inputs: list[str] = field(default_factory=list)
    requires_reference_data: bool = False

    @property
    def awaiting_project_threshold(self) -> bool:
        """True when the rule needs an approved project value before it can run.

        Such rules ship disabled. The platform never invents a project-specific
        safety threshold.
        """
        return self.threshold_source == "project" and self.threshold is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "enabled": self.enabled,
            "severity": self.severity,
            "blocks_processing": self.blocks_processing,
            "blocks_export": self.blocks_export,
            "requires_review": self.requires_review,
            "threshold": self.threshold,
            "threshold_source": self.threshold_source,
            "version": self.version,
            "inputs": list(self.inputs),
            "requires_reference_data": self.requires_reference_data,
            "awaiting_project_threshold": self.awaiting_project_threshold,
        }


@dataclass(frozen=True)
class CsvColumn:
    key: str
    header: str
    type: str
    required: bool
    enum: str | None = None


@dataclass(frozen=True)
class CsvTemplate:
    id: str
    name: str
    description: str
    version: str
    columns: list[CsvColumn]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "columns": [
                {"key": c.key, "header": c.header, "type": c.type, "required": c.required, "enum": c.enum}
                for c in self.columns
            ],
        }


class ConfigStore:
    """Thread-safe, reloadable view over the YAML configuration."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._taxonomy: dict[str, list[str]] = {}
        self._field_mapping: dict[str, Any] = {}
        self._rules_raw: dict[str, Any] = {}
        self._confidence: dict[str, Any] = {}
        self._redaction: dict[str, Any] = {}
        self._csv_templates: dict[str, CsvTemplate] = {}
        self._loaded = False

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            taxonomy_raw = _read_yaml(TAXONOMY_PATH)
            self._taxonomy = {
                key: [str(v) for v in value] for key, value in taxonomy_raw.items() if isinstance(value, list)
            }
            self._field_mapping = _read_yaml(FIELD_MAPPING_PATH)
            self._rules_raw = _read_yaml(VALIDATION_RULES_PATH)
            self._confidence = _read_yaml(CONFIDENCE_PATH)
            self._redaction = _read_yaml(REDACTION_PATH)
            self._csv_templates = self._load_csv_templates()
            self._loaded = True

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def reload(self) -> None:
        self.load()

    @staticmethod
    def _load_csv_templates() -> dict[str, CsvTemplate]:
        templates: dict[str, CsvTemplate] = {}
        if not CSV_TEMPLATE_DIR.is_dir():
            return templates
        for path in sorted(CSV_TEMPLATE_DIR.glob("*.yaml")):
            data = _read_yaml(path)
            if not data.get("id"):
                continue
            columns = [
                CsvColumn(
                    key=str(col["key"]),
                    header=str(col.get("header", col["key"])),
                    type=str(col.get("type", "string")),
                    required=bool(col.get("required", False)),
                    enum=col.get("enum"),
                )
                for col in data.get("columns", [])
                if isinstance(col, dict) and col.get("key")
            ]
            templates[str(data["id"])] = CsvTemplate(
                id=str(data["id"]),
                name=str(data.get("name", data["id"])),
                description=str(data.get("description", "")),
                version=str(data.get("version", "1.0.0")),
                columns=columns,
            )
        return templates

    # -- taxonomy --------------------------------------------------------
    @property
    def taxonomy(self) -> dict[str, list[str]]:
        self._ensure()
        return dict(self._taxonomy)

    def taxonomy_values(self, key: str) -> list[str]:
        self._ensure()
        return list(self._taxonomy.get(key, []))

    def is_valid_taxonomy_value(self, key: str, value: str) -> bool:
        values = self.taxonomy_values(key)
        if not values:
            # No fallback vocabulary for this key -> nothing to validate against.
            return True
        return value in values

    # -- field mapping ---------------------------------------------------
    @property
    def field_mapping(self) -> dict[str, Any]:
        self._ensure()
        return dict(self._field_mapping)

    def canonical_fields(self) -> dict[str, Any]:
        return dict(self.field_mapping.get("canonical_fields", {}))

    def value_maps(self) -> dict[str, dict[str, list[str]]]:
        return dict(self.field_mapping.get("value_maps", {}))

    def required_canonical_fields(self) -> list[str]:
        return [name for name, spec in self.canonical_fields().items() if spec.get("required")]

    # -- validation rules -------------------------------------------------
    @property
    def rule_catalogue_version(self) -> str:
        self._ensure()
        return str(self._rules_raw.get("catalogue_version", "0.0.0"))

    def rules(self) -> list[RuleDefinition]:
        self._ensure()
        out: list[RuleDefinition] = []
        for raw in self._rules_raw.get("rules", []):
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            out.append(
                RuleDefinition(
                    id=str(raw["id"]),
                    category=str(raw.get("category", "OTHER")),
                    description=str(raw.get("description", "")).strip(),
                    enabled=bool(raw.get("enabled", True)),
                    severity=str(raw.get("severity", "WARNING")).upper(),
                    blocks_processing=bool(raw.get("blocks_processing", False)),
                    blocks_export=bool(raw.get("blocks_export", False)),
                    requires_review=bool(raw.get("requires_review", False)),
                    threshold=raw.get("threshold"),
                    threshold_source=str(raw.get("threshold_source", "none")),
                    version=str(raw.get("version", "1.0")),
                    inputs=[str(i) for i in raw.get("inputs", [])],
                    requires_reference_data=bool(raw.get("requires_reference_data", False)),
                )
            )
        return out

    def rule_map(self) -> dict[str, RuleDefinition]:
        return {rule.id: rule for rule in self.rules()}

    def rule(self, rule_id: str) -> RuleDefinition | None:
        return self.rule_map().get(rule_id)

    # -- confidence -------------------------------------------------------
    @property
    def confidence(self) -> dict[str, Any]:
        self._ensure()
        return dict(self._confidence)

    def confidence_bands(self) -> dict[str, dict[str, Any]]:
        return dict(self.confidence.get("bands", {}))

    def confidence_fields(self) -> dict[str, dict[str, Any]]:
        return dict(self.confidence.get("fields", {}))

    def confidence_components(self) -> dict[str, dict[str, Any]]:
        return dict(self.confidence.get("components", {}))

    def confidence_hard_floor(self) -> float:
        return float(self.confidence.get("hard_floor", 0.5))

    def safety_critical_fields(self) -> set[str]:
        return {name for name, spec in self.confidence_fields().items() if spec.get("safety_critical")}

    # -- redaction --------------------------------------------------------
    @property
    def redaction(self) -> dict[str, Any]:
        self._ensure()
        return dict(self._redaction)

    # -- CSV templates ----------------------------------------------------
    def csv_templates(self) -> dict[str, CsvTemplate]:
        self._ensure()
        return dict(self._csv_templates)

    def csv_template(self, template_id: str) -> CsvTemplate | None:
        return self.csv_templates().get(template_id)

    # -- reproducibility --------------------------------------------------
    def rule_version_signature(self) -> str:
        """Stable, human-readable version stamp recorded on every record."""
        return f"rules-{self.rule_catalogue_version}"


_store = ConfigStore()


def get_config_store() -> ConfigStore:
    return _store
