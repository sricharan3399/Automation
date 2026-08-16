"""First-run database initialisation and built-in seed data.

Idempotent: safe to call on every application start.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, select, text
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
        # Seeded DISABLED with no directory. Previously this shipped enabled
        # with dataset_dir=None, which fell through to a settings default of
        # tests/golden_dataset - so a fresh install with no configured source
        # silently processed test fixtures as production events.
        "enabled": False,
        "last_status": "NOT_CONFIGURED",
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
        # Never seeded as a health result. Status is only ever written by an
        # actual probe (spec section 8); until one runs, nothing is known.
        "last_status": "NOT_TESTED",
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
        # Never seeded as a health result. Status is only ever written by an
        # actual probe (spec section 8); until one runs, nothing is known.
        "last_status": "NOT_TESTED",
        "settings_json": {"note": "Stream manifests are read from the event bundle supplied by the adapter."},
    },
    {
        "connection_id": "evidence_store",
        "display_name": "Evidence Storage",
        "kind": "evidence_store",
        "adapter": "local_evidence",
        "integration_type": "json_export",
        "enabled": True,
        # Never seeded as a health result. Status is only ever written by an
        # actual probe (spec section 8); until one runs, nothing is known.
        "last_status": "NOT_TESTED",
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
        session.add(ConnectionProfile(**_apply_explicit_dataset_dir(spec)))
        created["connection_profiles"] += 1

    return created


def _apply_explicit_dataset_dir(spec: dict[str, Any]) -> dict[str, Any]:
    """Enable the local-files source only when an operator explicitly set one.

    ``AV_LOCAL_DATASET_DIR`` being present in the environment is a deliberate
    act - somebody named a directory. Honouring it at seed time is what lets a
    tester (and CI) configure the source before first launch, without the
    platform ever *inventing* a dataset directory for itself.

    Applied only when the row is first created, so a tester's later edits on the
    Connections page are never overwritten.
    """
    if spec["connection_id"] != "local_files":
        return spec
    configured = get_settings().local_dataset_dir
    if configured is None:
        return spec
    seeded = dict(spec)
    seeded["enabled"] = True
    seeded["settings_json"] = {**spec["settings_json"], "dataset_dir": str(configured)}
    return seeded


# Columns added after the first release. ``create_all`` creates missing tables
# but never alters an existing one, so a database created by an earlier build
# would be missing these and every query against the table would fail. Kept
# deliberately tiny - additive, nullable columns only. Anything that needs to
# rewrite or drop data warrants a real migration tool, not this.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "connection_profiles": {
        "last_success_at": "DATETIME",
        "auth_status": "VARCHAR(32)",
    },
}


def apply_additive_migrations() -> list[str]:
    """Add any missing additive columns to an existing database."""
    engine = get_engine()
    inspector = inspect(engine)
    applied: list[str] = []
    existing_tables = set(inspector.get_table_names())

    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # create_all() will build it complete
        present = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl_type in columns.items():
            if name in present:
                continue
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
            applied.append(f"{table}.{name}")
    if applied:
        log.info("Applied additive migrations: %s", ", ".join(applied))
    return applied


def retire_unearned_statuses() -> int:
    """Downgrade connection statuses that no probe ever established.

    Earlier builds seeded ``last_status='CONFIGURED'`` at database creation, and
    the dashboard painted it the same green as CONNECTED - so a source nobody
    had ever contacted looked healthy. Fixing the seed only helps new installs;
    an upgraded database keeps the fabricated value forever.

    Scoped precisely by ``last_tested_at IS NULL``: a status that was actually
    earned by a probe has a timestamp and is left completely alone.
    """
    engine = get_engine()
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE connection_profiles SET last_status = 'NOT_TESTED' "
                "WHERE last_status = 'CONFIGURED' AND last_tested_at IS NULL"
            )
        )
    changed = int(result.rowcount or 0)
    if changed:
        log.info("Retired %d unearned CONFIGURED connection status(es)", changed)
    return changed


def initialize_database() -> dict[str, Any]:
    """Create the schema and seed built-ins. Idempotent."""
    create_all()
    migrated = apply_additive_migrations()
    retired = retire_unearned_statuses()
    engine = get_engine()
    tables = sorted(inspect(engine).get_table_names())
    with session_scope() as session:
        created = seed_builtins(session)
    log.info("Database ready: %d tables, seeded %s", len(tables), created)
    return {
        "tables": tables,
        "seeded": created,
        "migrated": migrated,
        "statuses_retired": retired,
        "database_url": get_settings().database_url,
    }
