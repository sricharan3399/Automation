"""Dynamic filter vocabulary, dependent filters and live record counts.

The dashboard dropdowns are populated from here. Values are source-derived
whenever the connected source can describe its own vocabulary; the bundled
taxonomy is used only as a labelled fallback, so a tester can always see which
options came from the real data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from backend.api.deps import DbSession, adapter_http_error
from backend.configstore import get_config_store
from backend.connectors.base import AdapterError
from backend.connectors.registry import ConnectionManager, resolve_filter_values
from backend.models.contracts import ScoutQuery
from backend.settings import get_settings

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


def _adapter(session: Any, connection_id: str | None):
    manager = ConnectionManager(session)
    resolved = connection_id or manager.default_event_source()
    adapter = manager.adapter_for(resolved)
    adapter.authenticate()
    return adapter, resolved


@router.get("/filters")
def filters(session: DbSession, connection_id: str | None = None) -> dict[str, Any]:
    """Full filter vocabulary, source-derived where possible."""
    settings = get_settings()
    try:
        adapter, resolved = _adapter(session, connection_id)
    except AdapterError as exc:
        # A missing source is not an error here: the UI still needs a vocabulary,
        # clearly labelled as fallback.
        payload = resolve_filter_values(None)
        payload["connection_id"] = connection_id
        payload["source_error"] = exc.user_message
        payload["countries"] = settings.raw.get("countries", {})
        return payload

    payload = resolve_filter_values(adapter)
    payload["connection_id"] = resolved
    payload["adapter"] = adapter.name
    payload["countries"] = settings.raw.get("countries", {})
    return payload


@router.post("/dependent-filters")
def dependent_filters(
    query: ScoutQuery,
    session: DbSession,
    connection_id: str | None = None,
) -> dict[str, Any]:
    """Narrow the vocabulary to what is still reachable given the current query.

    Selecting Germany refreshes the available cities, road types and datasets;
    selecting Bus refreshes the bus subtypes and scenario tags.
    """
    try:
        adapter, resolved = _adapter(session, connection_id)
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    payload = resolve_filter_values(adapter, query)
    payload["connection_id"] = resolved
    return payload


@router.post("/estimate")
def estimate(
    query: ScoutQuery,
    session: DbSession,
    connection_id: str | None = None,
) -> dict[str, Any]:
    """Lightweight matching-record count for the live counter on Scout Setup."""
    try:
        adapter, resolved = _adapter(session, connection_id)
        count, exact, note = adapter.estimate_count(query)
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    return {
        "connection_id": resolved,
        "estimated_records": count,
        "is_exact": exact,
        "note": note,
    }


@router.get("/values/{key}")
def values(key: str, session: DbSession, connection_id: str | None = None) -> dict[str, Any]:
    payload = filters(session, connection_id)
    entry = payload.get("fields", {}).get(key)
    if entry is None:
        available = ", ".join(sorted(payload.get("fields", {})))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No vocabulary for '{key}'. Available: {available}",
        )
    return {"key": key, **entry}


@router.get("/fallback")
def fallback() -> dict[str, Any]:
    """The bundled taxonomy, for reference and for offline configuration."""
    return {
        "taxonomy": get_config_store().taxonomy,
        "note": (
            "This is the bundled fallback vocabulary. When a source is connected its own "
            "vocabulary takes precedence and these values are only used to fill gaps."
        ),
    }
