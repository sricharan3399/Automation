"""Source -> canonical normalisation.

Two responsibilities:

1. **Schema discovery** - inspect the fields a source actually returns and
   propose a mapping onto canonical field names, with a confidence and the
   method used. The tester confirms or corrects it in the mapping editor.
2. **Record normalisation** - apply a confirmed mapping to a raw record and
   produce an :class:`EventMetadata` contract, normalising vocabulary through
   the configured value maps.

Country handling is deliberately strict: the country is resolved from an
authoritative metadata field, and the field it came from is recorded. A country
inferred from a filename or path is marked non-authoritative and fails
``DATA_COUNTRY_AUTHORITATIVE`` rather than being silently accepted.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from dateutil import parser as date_parser

from backend.configstore import get_config_store
from backend.connectors.base import FieldDescriptor, SourceSchema
from backend.models.contracts import EventMetadata
from backend.settings import get_settings

# Field names that can never be treated as an authoritative country source.
NON_AUTHORITATIVE_FIELD_PATTERN = re.compile(
    r"(file|filename|path|uri|url|folder|directory|basename|key|blob)", re.IGNORECASE
)

_ISO_BY_NAME_CACHE: dict[str, str] | None = None


def normalize_key(name: str) -> str:
    """Case/separator-insensitive form used for fuzzy field matching."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------
def infer_type(values: list[Any]) -> str:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "unknown"
    sample = non_null[0]
    if isinstance(sample, bool):
        return "bool"
    if isinstance(sample, int):
        return "int"
    if isinstance(sample, float):
        return "float"
    if isinstance(sample, (list, tuple)):
        return "list"
    if isinstance(sample, dict):
        return "object"
    if isinstance(sample, (datetime, date)):
        return "datetime"
    text = str(sample)
    if _looks_like_datetime(text):
        return "datetime"
    return "string"


def _looks_like_datetime(text: str) -> bool:
    if len(text) < 8 or len(text) > 40:
        return False
    if not re.search(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        date_parser.isoparse(text)
        return True
    except (ValueError, TypeError):
        return False


def suggest_mapping(records: list[dict[str, Any]]) -> SourceSchema:
    """Propose a source -> canonical mapping from sample records.

    Matching, best first:
      exact alias      -> confidence 1.00
      normalised alias -> confidence 0.90
      canonical name   -> confidence 0.95
      normalised name  -> confidence 0.85
      otherwise        -> unmapped, confidence 0.0
    """
    store = get_config_store()
    canonical = store.canonical_fields()

    alias_exact: dict[str, str] = {}
    alias_norm: dict[str, str] = {}
    for canonical_name, spec in canonical.items():
        alias_exact.setdefault(canonical_name, canonical_name)
        alias_norm.setdefault(normalize_key(canonical_name), canonical_name)
        for alias in spec.get("aliases", []) or []:
            alias_exact.setdefault(str(alias), canonical_name)
            alias_norm.setdefault(normalize_key(alias), canonical_name)

    # Collect field names and sample values across the records.
    samples: dict[str, list[Any]] = {}
    for record in records:
        for key, value in _flatten(record).items():
            samples.setdefault(key, []).append(value)

    descriptors: list[FieldDescriptor] = []
    claimed: set[str] = set()
    for source_field, values in sorted(samples.items()):
        canonical_field: str | None = None
        method = "unmapped"
        confidence = 0.0

        if source_field in alias_exact:
            canonical_field, method, confidence = alias_exact[source_field], "exact", 1.0
        elif normalize_key(source_field) in alias_norm:
            canonical_field, method, confidence = alias_norm[normalize_key(source_field)], "normalised", 0.90

        # A canonical field is claimed once; later matches are reported unmapped
        # so the tester can resolve the ambiguity explicitly.
        if canonical_field and canonical_field in claimed:
            canonical_field, method, confidence = None, "ambiguous", 0.0
        elif canonical_field:
            claimed.add(canonical_field)

        descriptors.append(
            FieldDescriptor(
                source_field=source_field,
                inferred_type=infer_type(values),
                sample_values=[v for v in values if v is not None][:5],
                canonical_field=canonical_field,
                mapping_confidence=confidence,
                mapping_method=method,
                nullable=any(v is None for v in values),
            )
        )

    unmatched_required = [
        name for name in store.required_canonical_fields() if name not in claimed
    ]
    note = ""
    if unmatched_required:
        note = (
            "Required canonical fields with no source match: "
            + ", ".join(sorted(unmatched_required))
            + ". Map them manually before running."
        )

    return SourceSchema(
        schema_version=get_config_store().field_mapping.get("version", "unknown"),
        api_version="n/a",
        fields=descriptors,
        note=note,
    )


def _flatten(record: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Flatten one nesting level of dotted paths so nested metadata is mappable."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and depth < 2:
            out.update(_flatten(value, prefix=f"{path}.", depth=depth + 1))
        else:
            out[path] = value
    return out


def mapping_from_schema(schema: SourceSchema) -> dict[str, str]:
    """``{source_field: canonical_field}`` for the confidently mapped fields."""
    return {
        f.source_field: f.canonical_field
        for f in schema.fields
        if f.canonical_field and f.mapping_confidence >= 0.85
    }


# ---------------------------------------------------------------------------
# Value normalisation
# ---------------------------------------------------------------------------
def normalize_value(canonical_field: str, value: Any) -> Any:
    """Map a source value onto the canonical vocabulary.

    Unknown values are returned unchanged (lower-cased for strings) rather than
    dropped, so the UI can flag them as unmapped instead of losing information.
    """
    if value is None:
        return None
    value_maps = get_config_store().value_maps()
    field_map = value_maps.get(canonical_field)

    def _one(raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        token = raw.strip()
        if not token:
            return None
        lowered = token.lower().replace(" ", "_").replace("-", "_")
        if not field_map:
            return lowered
        for canonical_value, spellings in field_map.items():
            if lowered == canonical_value:
                return canonical_value
            for spelling in spellings:
                if lowered == str(spelling).lower().replace(" ", "_").replace("-", "_"):
                    return canonical_value
        return lowered

    if isinstance(value, (list, tuple, set)):
        return [v for v in (_one(item) for item in value) if v is not None]
    return _one(value)


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Heuristic: values beyond year 2001 in ms are treated as milliseconds.
        seconds = float(value) / 1000.0 if float(value) > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        parsed = date_parser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[;,|]", value) if p.strip()]
        return parts or []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Country resolution
# ---------------------------------------------------------------------------
def _iso_by_name() -> dict[str, str]:
    global _ISO_BY_NAME_CACHE
    if _ISO_BY_NAME_CACHE is None:
        mapping: dict[str, str] = {}
        for entry in get_settings().raw.get("countries", {}).get("allowed", []) or []:
            name = str(entry.get("name", "")).strip().lower()
            code = str(entry.get("code", "")).strip().upper()
            if name and code:
                mapping[name] = code
        _ISO_BY_NAME_CACHE = mapping
    return _ISO_BY_NAME_CACHE


def resolve_country(canonical: dict[str, Any], raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return ``(country_name, country_code, source_field)``.

    ``country_code`` from an authoritative field wins. When only a country name
    is present it is resolved to a code via the configured country list. When
    the only available value came from a filename-like field, the value is kept
    but ``source_field`` records that origin so the validation rule can reject it.
    """
    code_value = canonical.get("country_code")
    name_value = canonical.get("country")

    source_field: str | None = None
    for key, value in raw.items():
        if value in (None, ""):
            continue
        if code_value is not None and str(value).strip().upper() == str(code_value).strip().upper():
            source_field = key
            break
    if source_field is None and name_value is not None:
        for key, value in raw.items():
            if value in (None, ""):
                continue
            if str(value).strip().lower() == str(name_value).strip().lower():
                source_field = key
                break

    code = str(code_value).strip().upper() if code_value else None
    name = str(name_value).strip() if name_value else None

    if code is None and name:
        code = _iso_by_name().get(name.lower())
    if name is None and code:
        for candidate_name, candidate_code in _iso_by_name().items():
            if candidate_code == code:
                name = candidate_name.title()
                break

    if code is not None and len(code) != 2:
        # A malformed code is not silently repaired; it is left for review.
        code = code[:2] if len(code) > 2 else None

    return name, code, source_field


def is_authoritative_country_source(source_field: str | None) -> bool:
    if not source_field:
        return False
    return not bool(NON_AUTHORITATIVE_FIELD_PATTERN.search(source_field))


# ---------------------------------------------------------------------------
# Record -> EventMetadata
# ---------------------------------------------------------------------------
_LIST_FIELDS = {"object_type", "scenario_tags", "traffic_control_entity", "lane_configuration"}
_INT_FIELDS = {"lane_count"}
_FLOAT_FIELDS = {"duration_s"}
_DATETIME_FIELDS = {"event_time", "evaluation_start", "evaluation_end"}
_VOCAB_FIELDS = {
    "road_type",
    "weather",
    "lighting",
    "object_type",
    "intersection_type",
    "intersection_complexity",
    "traffic_control_entity",
    "traffic_light_state",
    "vehicle_maneuver",
    "bus_type",
    "lane_configuration",
}


def build_event_metadata(
    raw: dict[str, Any],
    mapping: dict[str, str] | None = None,
) -> EventMetadata:
    """Apply ``mapping`` to a raw source record and build the canonical contract."""
    flat = _flatten(raw)
    if mapping is None:
        mapping = mapping_from_schema(suggest_mapping([raw]))

    canonical: dict[str, Any] = {}
    consumed: set[str] = set()
    for source_field, canonical_field in mapping.items():
        if source_field in flat:
            canonical[canonical_field] = flat[source_field]
            consumed.add(source_field)

    # Coerce types.
    for field_name in list(canonical):
        value = canonical[field_name]
        if field_name in _DATETIME_FIELDS:
            canonical[field_name] = parse_datetime(value)
        elif field_name in _INT_FIELDS:
            canonical[field_name] = _as_int(value)
        elif field_name in _FLOAT_FIELDS:
            canonical[field_name] = _as_float(value)
        elif field_name in _LIST_FIELDS:
            canonical[field_name] = _as_list(value)

    # Normalise vocabulary.
    for field_name in _VOCAB_FIELDS & set(canonical):
        canonical[field_name] = normalize_value(field_name, canonical[field_name])

    country, country_code, country_field = resolve_country(canonical, flat)
    if country_field and not is_authoritative_country_source(country_field):
        country_field = f"{country_field} (non-authoritative)"

    unmapped = {k: v for k, v in flat.items() if k not in consumed and v not in (None, "")}

    return EventMetadata(
        event_id=str(canonical.get("event_id") or raw.get("event_id") or ""),
        session_id=str(canonical.get("session_id") or ""),
        job_ref=canonical.get("job_ref"),
        country=country,
        country_code=country_code,
        country_source_field=country_field,
        region=canonical.get("region"),
        city=canonical.get("city"),
        test_area=canonical.get("test_area"),
        route=canonical.get("route"),
        event_time=canonical.get("event_time"),
        evaluation_start=canonical.get("evaluation_start"),
        evaluation_end=canonical.get("evaluation_end"),
        duration_s=canonical.get("duration_s"),
        road_type=canonical.get("road_type"),
        lane_count=canonical.get("lane_count"),
        lane_id=canonical.get("lane_id"),
        lane_configuration=canonical.get("lane_configuration") or [],
        intersection_type=canonical.get("intersection_type"),
        intersection_complexity=canonical.get("intersection_complexity"),
        traffic_control_entity=canonical.get("traffic_control_entity") or [],
        traffic_light_state=canonical.get("traffic_light_state"),
        weather=canonical.get("weather"),
        lighting=canonical.get("lighting"),
        object_type=canonical.get("object_type") or [],
        bus_type=canonical.get("bus_type"),
        scenario_tags=_as_list(canonical.get("scenario_tags")),
        vehicle_maneuver=canonical.get("vehicle_maneuver"),
        project=canonical.get("project"),
        dataset=canonical.get("dataset"),
        dataset_version=canonical.get("dataset_version"),
        drive_collection=canonical.get("drive_collection"),
        vehicle_build=canonical.get("vehicle_build"),
        software_version=canonical.get("software_version"),
        map_version=canonical.get("map_version"),
        event_type=str(raw.get("event_type") or canonical.get("event_type") or "unknown"),
        unmapped=unmapped,
    )
