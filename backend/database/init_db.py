"""First-run database initialisation and built-in seed data.

Idempotent: safe to call on every application start.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from backend.database.session import get_engine, session_scope
from backend.models.contracts import (
    ScoutQuery,
    SensorConfiguration,
    StreamRequirement,
    StreamRequirementSpec,
)
from backend.models.orm import Base, ConfigurationProfile, ConnectionProfile
from backend.settings import get_settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Built-in configuration profiles
# ---------------------------------------------------------------------------
def _germany_bus_profile() -> dict[str, Any]:
    """The bundled 'Germany Bus Validation' template (spec section 110).

    Deliberately permissive: every scene filter is 'Any'. No project-specific
    failure thresholds are baked in - those arrive only when approved values
    are supplied by the project.
    """
    query = ScoutQuery(
        country_code="DE",
        country="Germany",
        object_types=["bus"],
    )
    sensors = SensorConfiguration(
        streams=[
            StreamRequirementSpec(stream_type="vehicle_state", requirement=StreamRequirement.REQUIRED),
            StreamRequirementSpec(stream_type="localization", requirement=StreamRequirement.OPTIONAL),
            StreamRequirementSpec(
                stream_type="camera", camera_position="front_main", requirement=StreamRequirement.OPTIONAL
            ),
            StreamRequirementSpec(
                stream_type="camera", camera_position="front_wide", requirement=StreamRequirement.OPTIONAL
            ),
            StreamRequirementSpec(stream_type="perception", requirement=StreamRequirement.OPTIONAL),
            StreamRequirementSpec(stream_type="map", requirement=StreamRequirement.OPTIONAL),
        ]
    )
    return {
        "profile_id": "germany_bus_validation",
        "name": "Germany Bus Validation",
        "description": (
            "Bundled template for German bus-scenario validation. Human review enabled, "
            "production submission disabled, no project-specific failure thresholds applied."
        ),
        "query_json": query.model_dump(mode="json"),
        "sensor_config_json": sensors.model_dump(mode="json"),
        "csv_template_id": "germany_bus_test",
        "evidence_config_json": {"enabled": True, "redaction_required_for_export": True},
        "rule_overrides_json": {},
        "threshold_overrides_json": {},
        "is_builtin": True,
    }


def _variant(profile_id: str, name: str, description: str, **query_overrides: Any) -> dict[str, Any]:
    base = _germany_bus_profile()
    query = ScoutQuery(**{**base["query_json"], **query_overrides})
    return {
        **base,
        "profile_id": profile_id,
        "name": name,
        "description": description,
        "query_json": query.model_dump(mode="json"),
    }


BUILTIN_PROFILES: list[dict[str, Any]] = [
    _germany_bus_profile(),
    _variant(
        "germany_bus_urban",
        "Germany Bus Urban",
        "German bus scenarios restricted to urban and residential roads.",
        road_types=["urban", "residential"],
    ),
    _variant(
        "germany_bus_autobahn",
        "Germany Bus Autobahn",
        "German bus scenarios on Autobahn, motorway and ramps.",
        road_types=["autobahn", "motorway", "ramp"],
    ),
    _variant(
        "germany_traffic_light",
        "Germany Traffic Light",
        "Signalised junction interactions in Germany.",
        traffic_control_entities=["traffic_light"],
    ),
    _variant(
        "germany_complex_junction",
        "Germany Complex Junction",
        "Multi-leg and roundabout junctions with elevated complexity.",
        intersection_types=["multi_leg_junction", "roundabout", "four_way_junction"],
        intersection_complexity=["complex", "very_complex"],
    ),
    _variant(
        "night_bus_scenarios",
        "Night Bus Scenarios",
        "Bus scenarios recorded at night or in low light.",
        lighting=["night", "low_light", "dusk", "dawn"],
    ),
    _variant(
        "rain_bus_scenarios",
        "Rain Bus Scenarios",
        "Bus scenarios in rain, heavy rain or on wet roads.",
        weather=["rain", "heavy_rain", "wet_road"],
    ),
]


# ---------------------------------------------------------------------------
# Built-in connection profiles
# ---------------------------------------------------------------------------
BUILTIN_CONNECTIONS: list[dict[str, Any]] = [
    {
        "connection_id": "nvidia_data_scout",
        "display_name": "NVIDIA / Internal Data Scout",
        "kind": "data_scout",
        "adapter": "nvidia_internal_data_scout",
        "integration_type": "rest_api",
        "enabled": False,
        "last_status": "NOT_CONFIGURED",
        "settings_json": {
            "base_url": None,
            "auth_mode": "none",
            "verify_tls": True,
            "timeout_seconds": 60,
            "note": (
                "Awaiting approved endpoint, authentication mode and schema. "
                "The adapter refuses to operate until these are supplied - it never "
                "fabricates a connection or results."
            ),
        },
    },
    {
        "connection_id": "local_files",
        "display_name": "Local CSV / JSON Dataset",
        "kind": "metadata_api",
        "adapter": "local_files",
        "integration_type": "json_export",
        "enabled": True,
        "last_status": "CONFIGURED",
        "settings_json": {
            "dataset_dir": None,
            "note": "Reads approved exported CSV/JSON event bundles from a local directory.",
        },
    },
    {
        "connection_id": "synthetic_demo",
        "display_name": "Synthetic Test Dataset (DEMO ONLY)",
        "kind": "metadata_api",
        "adapter": "synthetic",
        "integration_type": "json_export",
        "enabled": False,
        "last_status": "DEMO_ONLY",
        "settings_json": {
            "note": "Deterministic synthetic events. Refused while AV_MODE=production.",
        },
    },
    {
        "connection_id": "map_service",
        "display_name": "HD Map Service",
        "kind": "map_service",
        "adapter": "bundle_map",
        "integration_type": "rest_api",
        "enabled": True,
        "last_status": "CONFIGURED",
        "settings_json": {
            "base_url": None,
            "note": "Falls back to map context carried inside the event bundle when no service is configured.",
        },
    },
    {
        "connection_id": "sensor_store",
        "display_name": "Sensor Data Source",
        "kind": "sensor_store",
        "adapter": "bundle_sensors",
        "integration_type": "json_export",
        "enabled": True,
        "last_status": "CONFIGURED",
        "settings_json": {"note": "Stream manifests are read from the event bundle supplied by the adapter."},
    },
    {
        "connection_id": "evidence_store",
        "display_name": "Evidence Storage",
        "kind": "evidence_store",
        "adapter": "local_evidence",
        "integration_type": "json_export",
        "enabled": True,
        "last_status": "CONFIGURED",
        "settings_json": {"note": "Local output directory. Raw evidence never leaves the approved environment."},
    },
    {
        "connection_id": "object_store",
        "display_name": "Object Storage",
        "kind": "object_store",
        "adapter": "not_configured",
        "integration_type": "rest_api",
        "enabled": False,
        "last_status": "NOT_CONFIGURED",
        "settings_json": {"note": "Optional. Required only for remote evidence or frame retrieval."},
    },
    {
        "connection_id": "labeling_tool",
        "display_name": "Internal Labeling Tool",
        "kind": "labeling_tool",
        "adapter": "not_configured",
        "integration_type": "rest_api",
        "enabled": False,
        "last_status": "NOT_CONFIGURED",
        "settings_json": {"note": "Optional. Read-only reference-annotation source."},
    },
    {
        "connection_id": "spreadsheet_service",
        "display_name": "Spreadsheet Service (optional)",
        "kind": "spreadsheet",
        "adapter": "not_configured",
        "integration_type": "rest_api",
        "enabled": False,
        "last_status": "NOT_CONFIGURED",
        "settings_json": {"note": "Optional. Disabled by default; production submission is disabled."},
    },
]


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def create_all() -> None:
    settings = get_settings()
    settings.ensure_directories()
    engine = get_engine()
    Base.metadata.create_all(engine)


def seed_builtins(session: Session) -> dict[str, int]:
    """Insert built-in profiles that do not exist yet.

    Existing rows are left untouched so a tester's edits to a built-in profile
    survive an upgrade.
    """
    created = {"configuration_profiles": 0, "connection_profiles": 0}

    existing_profiles = set(session.scalars(select(ConfigurationProfile.profile_id)).all())
    for spec in BUILTIN_PROFILES:
        if spec["profile_id"] in existing_profiles:
            continue
        session.add(ConfigurationProfile(**spec))
        created["configuration_profiles"] += 1

    existing_connections = set(session.scalars(select(ConnectionProfile.connection_id)).all())
    for spec in BUILTIN_CONNECTIONS:
        if spec["connection_id"] in existing_connections:
            continue
        session.add(ConnectionProfile(**spec))
        created["connection_profiles"] += 1

    return created


def initialize_database() -> dict[str, Any]:
    """Create the schema and seed built-ins. Idempotent."""
    create_all()
    engine = get_engine()
    tables = sorted(inspect(engine).get_table_names())
    with session_scope() as session:
        created = seed_builtins(session)
    log.info("Database ready: %d tables, seeded %s", len(tables), created)
    return {"tables": tables, "seeded": created, "database_url": get_settings().database_url}
