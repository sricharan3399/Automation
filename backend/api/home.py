"""Home dashboard aggregate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import desc, func, select

from backend.api.deps import CurrentIdentity, DbSession
from backend.configstore import get_config_store
from backend.connectors.registry import ConnectionManager
from backend.models.orm import AutomationRun, ConfigurationProfile, Event
from backend.settings import get_settings
from backend.version import CONTRACT_VERSION, SOFTWARE_VERSION

router = APIRouter(prefix="/home", tags=["home"])


@router.get("")
def home(session: DbSession, identity: CurrentIdentity) -> dict[str, Any]:
    """Everything the Home page shows, in one round trip."""
    settings = get_settings()
    manager = ConnectionManager(session)

    connections = [
        {
            "connection_id": p.connection_id,
            "display_name": p.display_name,
            "kind": p.kind,
            "enabled": p.enabled,
            "status": p.last_status,
            "connected": p.last_status == "CONNECTED",
            "last_tested_at": p.last_tested_at,
            "latency_ms": p.last_latency_ms,
            "error": p.last_error,
        }
        for p in manager.profiles()
    ]

    from backend.api.system import detect_gpu

    gpu = detect_gpu()

    last_run = session.scalar(select(AutomationRun).order_by(desc(AutomationRun.created_at)).limit(1))
    previous_run = None
    if last_run is not None:
        previous_run = {
            "run_id": last_run.run_id,
            "status": last_run.status,
            "dry_run": last_run.dry_run,
            "records_scanned": last_run.records_scanned,
            "records_matched_country": last_run.records_matched_country,
            "records_matched_scenario": last_run.records_matched_scenario,
            "candidate_issue_count": last_run.candidate_issue_count,
            "review_required_count": last_run.review_required_count,
            "blocking_error_count": last_run.blocking_error_count,
            "duplicates_merged": last_run.duplicates_merged,
            "csv_rows_created": last_run.csv_rows_created,
            "runtime_seconds": last_run.elapsed_seconds,
            "output_dir": last_run.output_dir,
            "finished_at": last_run.finished_at,
        }

    default_profile = session.scalar(
        select(ConfigurationProfile).where(ConfigurationProfile.profile_id == "germany_bus_validation")
    )
    current_configuration = None
    if default_profile is not None:
        query = default_profile.query_json or {}
        current_configuration = {
            "profile_id": default_profile.profile_id,
            "name": default_profile.name,
            "country": query.get("country") or query.get("country_code") or "Any",
            "objects": query.get("object_types") or ["Any"],
            "road_types": query.get("road_types") or ["Any"],
            "lane_count": (query.get("lanes") or {}).get("lane_count_exact") or "Any",
            "intersection": query.get("intersection_types") or ["Any"],
            "traffic_control": query.get("traffic_control_entities") or ["Any"],
            "weather": query.get("weather") or ["Any"],
            "lighting": query.get("lighting") or ["Any"],
            "date_range": {
                "start": (query.get("time_range") or {}).get("start_date"),
                "end": (query.get("time_range") or {}).get("end_date"),
            },
        }

    review_pending = session.scalar(
        select(func.count(Event.event_pk)).where(Event.review_required.is_(True))
    ) or 0
    blocked = session.scalar(
        select(func.count(Event.event_pk)).where(Event.status == "BLOCKED_DATA_ERROR")
    ) or 0

    return {
        "identity": identity.to_dict(),
        "versions": {
            "software": SOFTWARE_VERSION,
            "contract": CONTRACT_VERSION,
            "rules": get_config_store().rule_version_signature(),
        },
        "mode": {
            "operating_mode": settings.operating_mode,
            "source_access_mode": settings.source_access_mode,
            "production_submission_enabled": settings.allow_production_submission,
            "demo": settings.is_demo_mode,
        },
        "connections": connections,
        "gpu": {"available": gpu["available"], "detail": gpu["detail"], "devices": gpu["devices"]},
        "current_configuration": current_configuration,
        "previous_run": previous_run,
        "queues": {"review_required": review_pending, "blocked_data_errors": blocked},
        "quick_actions": [
            {"id": "new_run", "label": "NEW SCOUT RUN", "enabled": True},
            {"id": "repeat_last", "label": "REPEAT LAST RUN", "enabled": last_run is not None},
            {"id": "review_queue", "label": "OPEN REVIEW QUEUE", "enabled": review_pending > 0},
            {
                "id": "download_csv",
                "label": "DOWNLOAD LAST CSV",
                "enabled": bool(last_run and last_run.csv_rows_created),
            },
            {"id": "view_errors", "label": "VIEW ERRORS", "enabled": blocked > 0},
            {"id": "connection_test", "label": "CONNECTION TEST", "enabled": True},
        ],
    }
