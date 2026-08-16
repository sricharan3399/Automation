"""Adapter registry and connection manager.

Owns the mapping from a stored :class:`ConnectionProfile` to a live adapter
instance, and the resolution of "which vocabulary do the dashboard dropdowns
show" (source-derived first, bundled taxonomy only as a labelled fallback).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.configstore import get_config_store
from backend.connectors.base import (
    AdapterError,
    AdapterNotConfigured,
    ConnectionStatus,
    DataScoutAdapter,
    SupportedFilters,
)
from backend.connectors.data_scout import NvidiaInternalDataScoutAdapter
from backend.connectors.local_files import LocalFilesAdapter
from backend.connectors.synthetic import SyntheticAdapter
from backend.models.contracts import ScoutQuery
from backend.models.orm import ConnectionProfile
from backend.settings import get_settings

log = logging.getLogger(__name__)

AdapterFactory = Callable[[dict[str, Any]], DataScoutAdapter]

_FACTORIES: dict[str, AdapterFactory] = {
    LocalFilesAdapter.name: lambda cfg: LocalFilesAdapter(cfg),
    SyntheticAdapter.name: lambda cfg: SyntheticAdapter(cfg),
    NvidiaInternalDataScoutAdapter.name: lambda cfg: NvidiaInternalDataScoutAdapter(cfg),
}

#: Connection kinds that are event sources (as opposed to supporting services).
EVENT_SOURCE_KINDS = {"data_scout", "metadata_api"}

#: Supporting services are not event sources and have no adapter of their own.
#: Their "connection test" reports how the platform will actually behave, which
#: is more useful than pretending they route through the event-source registry.
SERVICE_ADAPTERS: dict[str, tuple[str, str]] = {
    "bundle_map": (
        "CONFIGURED",
        "No external HD map service is configured. Map context is read from the event bundle; "
        "events without map context are reported as such rather than analysed without a map.",
    ),
    "bundle_sensors": (
        "CONFIGURED",
        "Stream manifests are read from the event bundle supplied by the event source.",
    ),
    "local_evidence": (
        "CONFIGURED",
        "Evidence is written to the local output directory. Raw evidence never leaves the "
        "approved environment.",
    ),
    "not_configured": (
        "NOT_CONFIGURED",
        "This optional service has not been configured. It is not required for metadata, "
        "geometry, validation or CSV export.",
    ),
}


class UnknownAdapter(AdapterError):
    user_message = "The configured adapter is not available in this build."


def available_adapters() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "display_name": factory({}).display_name,
            "is_synthetic": factory({}).is_synthetic,
            "demo_only": factory({}).demo_only,
        }
        for name, factory in sorted(_FACTORIES.items())
    ]


def build_adapter(adapter_name: str, settings: dict[str, Any] | None = None) -> DataScoutAdapter:
    factory = _FACTORIES.get(adapter_name)
    if factory is None:
        raise UnknownAdapter(
            f"unknown adapter '{adapter_name}'",
            user_message=(
                f"No adapter named '{adapter_name}' exists in this build. "
                f"Available: {', '.join(sorted(_FACTORIES))}."
            ),
        )
    return factory(dict(settings or {}))


class ConnectionManager:
    """Resolves connection profiles into adapters and records test results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- profiles --------------------------------------------------------
    def profiles(self) -> list[ConnectionProfile]:
        return list(self.session.scalars(select(ConnectionProfile).order_by(ConnectionProfile.connection_pk)).all())

    def profile(self, connection_id: str) -> ConnectionProfile | None:
        return self.session.scalar(
            select(ConnectionProfile).where(ConnectionProfile.connection_id == connection_id)
        )

    def event_source_profiles(self) -> list[ConnectionProfile]:
        return [p for p in self.profiles() if p.kind in EVENT_SOURCE_KINDS]

    # -- adapters --------------------------------------------------------
    def adapter_for(self, connection_id: str) -> DataScoutAdapter:
        profile = self.profile(connection_id)
        if profile is None:
            raise AdapterNotConfigured(
                f"no connection profile '{connection_id}'",
                user_message=f"Connection '{connection_id}' does not exist. Create it on the Connections page.",
            )
        settings = dict(profile.settings_json or {})
        settings["enabled"] = profile.enabled
        settings["integration_type"] = profile.integration_type
        if profile.field_mapping_json:
            settings["field_mapping"] = profile.field_mapping_json
        return build_adapter(profile.adapter, settings)

    def default_event_source(self) -> str:
        """Pick the connection a run uses when the tester did not choose one.

        Preference order: an enabled+connected Data Scout, then any enabled
        event source. Synthetic is only chosen when the platform is explicitly
        in DEMO MODE.
        """
        profiles = self.event_source_profiles()
        demo = get_settings().is_demo_mode

        for profile in profiles:
            if profile.kind == "data_scout" and profile.enabled and profile.last_status == "CONNECTED":
                return profile.connection_id
        for profile in profiles:
            if profile.enabled and profile.adapter != SyntheticAdapter.name:
                return profile.connection_id
        if demo:
            for profile in profiles:
                if profile.adapter == SyntheticAdapter.name:
                    return profile.connection_id
        raise AdapterNotConfigured(
            "no enabled event source",
            user_message=(
                "No data source is enabled. Open Connections and enable an approved source, "
                "or point the Local CSV / JSON adapter at an approved exported dataset."
            ),
        )

    # -- testing ---------------------------------------------------------
    def test(self, connection_id: str) -> ConnectionStatus:
        profile = self.profile(connection_id)
        if profile is None:
            return ConnectionStatus(
                connected=False, status="NOT_CONFIGURED", message=f"Connection '{connection_id}' does not exist."
            )

        service = SERVICE_ADAPTERS.get(profile.adapter)
        if service is not None:
            state, message = service
            status = self._probe_service(profile, state, message)
            self._record(profile, status)
            return status

        try:
            adapter = self.adapter_for(connection_id)
            status = adapter.test_connection()
        except AdapterError as exc:
            status = ConnectionStatus(connected=False, status="ERROR", message=exc.user_message)
        except Exception as exc:  # defensive: never surface a raw traceback to the UI
            log.exception("Unexpected error testing connection %s", connection_id)
            status = ConnectionStatus(
                connected=False,
                status="ERROR",
                message=f"Unexpected error while testing this connection: {exc}",
            )

        self._record(profile, status)
        return status

    @staticmethod
    def _probe_service(profile: ConnectionProfile, state: str, message: str) -> ConnectionStatus:
        """Report a supporting service's real posture without inventing a client."""
        if profile.adapter == "local_evidence":
            output_dir = get_settings().output_dir
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                probe = output_dir / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
            except OSError as exc:
                return ConnectionStatus(
                    connected=False,
                    status="ERROR",
                    message=f"The evidence output directory is not writable: {exc}",
                )
        if profile.adapter == "bundle_map" and (profile.settings_json or {}).get("base_url"):
            return ConnectionStatus(
                connected=True,
                status="CONFIGURED",
                message=(
                    "An HD map service URL is configured; it is queried per event when the bundle "
                    "carries no map context."
                ),
            )
        return ConnectionStatus(connected=state == "CONFIGURED", status=state, message=message)

    def _record(self, profile: ConnectionProfile, status: ConnectionStatus) -> None:
        profile.last_tested_at = datetime.now(timezone.utc)
        profile.last_status = status.status
        profile.last_latency_ms = status.latency_ms
        profile.last_error = None if status.connected else status.message
        profile.api_version = status.api_version
        profile.schema_version = status.schema_version
        profile.permissions_json = list(status.permissions)
        self.session.add(profile)

    def test_all(self) -> dict[str, ConnectionStatus]:
        return {p.connection_id: self.test(p.connection_id) for p in self.profiles()}


# ---------------------------------------------------------------------------
# Filter vocabulary resolution
# ---------------------------------------------------------------------------
def resolve_filter_values(
    adapter: DataScoutAdapter | None,
    query: ScoutQuery | None = None,
) -> dict[str, Any]:
    """Return the vocabulary for every dashboard dropdown.

    Each key reports where its values came from, so the UI can label anything
    that is not source-derived. Source values win; the bundled taxonomy fills
    only the gaps.
    """
    store = get_config_store()
    fallback = store.taxonomy

    source_values: dict[str, list[str]] = {}
    origin_note = ""
    source_available = False
    if adapter is not None:
        try:
            supported: SupportedFilters = adapter.get_supported_filters(query)
            if supported.origin == "source":
                source_values = supported.values
                source_available = True
            origin_note = supported.note
        except AdapterError as exc:
            origin_note = f"Source vocabulary unavailable: {exc.user_message}"
        except Exception as exc:  # defensive
            log.warning("Filter discovery failed: %s", exc)
            origin_note = f"Source vocabulary unavailable: {exc}"

    result: dict[str, Any] = {}
    for key in sorted(set(fallback) | set(source_values)):
        if key in source_values and source_values[key]:
            result[key] = {"values": source_values[key], "origin": "source"}
        else:
            result[key] = {"values": fallback.get(key, []), "origin": "fallback"}

    return {
        "fields": result,
        "source_available": source_available,
        "note": origin_note
        or (
            "Values marked 'fallback' come from the bundled taxonomy because the source did not "
            "supply its own vocabulary for that field."
        ),
    }
