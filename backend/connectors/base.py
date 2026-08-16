"""Data-source adapter interface.

Every source - the NVIDIA / internal Data Scout, a local export, an approved
SDK - is reached through :class:`DataScoutAdapter`. Nothing above this layer
knows which source it is talking to.

Design rules enforced here:

* An adapter that is not configured raises :class:`AdapterNotConfigured`.
  It never returns invented data, and it never reports a healthy connection.
* Every capability an adapter cannot provide is reported as *unavailable with a
  reason*, so the UI can say "no reference data for this event" rather than
  quietly producing an empty result that reads like "no findings".
* Adapters are READ ONLY. There is no write, delete or annotate method on this
  interface at all, so no caller can accidentally mutate source data.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.models.contracts import (
    DetectionContract,
    EventBundle,
    EventMetadata,
    MapContext,
    PoseSample,
    ScoutQuery,
    StreamManifestEntry,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AdapterError(Exception):
    """Base class for adapter failures.

    ``user_message`` is what the dashboard shows; it must be actionable and
    never a bare HTTP status code.
    """

    user_message = "The data source reported an error."
    retryable = False

    def __init__(self, message: str, *, user_message: str | None = None, detail: Any = None) -> None:
        super().__init__(message)
        if user_message:
            self.user_message = user_message
        self.detail = detail


class AdapterNotConfigured(AdapterError):
    user_message = (
        "This data source has not been configured yet. Open Connections and supply the "
        "approved endpoint and authentication details."
    )


class AdapterAuthError(AdapterError):
    user_message = "Authentication with the data source failed. Check your credentials and permissions."


class AdapterUnavailable(AdapterError):
    user_message = "The data source is temporarily unavailable. Progress has been checkpointed."
    retryable = True


class AdapterRateLimited(AdapterUnavailable):
    user_message = "The data source is rate limiting requests. The run will back off and retry."
    retryable = True


class AdapterSchemaError(AdapterError):
    user_message = (
        "The data source returned a structure this version does not recognise. "
        "Re-run Discover Schema and confirm the field mapping."
    )


class AdapterPermissionError(AdapterError):
    user_message = "Your account is not permitted to access the requested dataset."


class DemoDataRefused(AdapterError):
    user_message = (
        "Synthetic data is not available while the platform is in production mode. "
        "Switch to DEMO MODE explicitly, or connect an approved data source."
    )


# ---------------------------------------------------------------------------
# Capability description
# ---------------------------------------------------------------------------
@dataclass
class FieldDescriptor:
    """One field discovered on the source."""

    source_field: str
    inferred_type: str = "string"
    sample_values: list[Any] = field(default_factory=list)
    canonical_field: str | None = None
    mapping_confidence: float = 0.0
    mapping_method: str = "unmapped"  # exact | normalised | alias | manual | unmapped
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_field": self.source_field,
            "inferred_type": self.inferred_type,
            "sample_values": self.sample_values[:5],
            "canonical_field": self.canonical_field,
            "mapping_confidence": round(self.mapping_confidence, 3),
            "mapping_method": self.mapping_method,
            "nullable": self.nullable,
        }


@dataclass
class SourceSchema:
    schema_version: str = "unknown"
    api_version: str = "unknown"
    fields: list[FieldDescriptor] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "api_version": self.api_version,
            "discovered_at": self.discovered_at.isoformat(),
            "note": self.note,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class SupportedFilters:
    """The source's own vocabulary.

    ``origin`` distinguishes source-derived values from the bundled fallback so
    the dashboard can label them. Hard-coded lists are only ever a fallback.
    """

    values: dict[str, list[str]] = field(default_factory=dict)
    origin: str = "source"  # source | fallback | mixed
    supports_count_estimate: bool = False
    supports_pagination: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": {k: list(v) for k, v in self.values.items()},
            "origin": self.origin,
            "supports_count_estimate": self.supports_count_estimate,
            "supports_pagination": self.supports_pagination,
            "note": self.note,
        }


@dataclass
class ConnectionStatus:
    connected: bool
    status: str  # CONNECTED | DISCONNECTED | NOT_CONFIGURED | AUTH_FAILED | DEMO_ONLY | ERROR
    message: str
    latency_ms: float | None = None
    api_version: str | None = None
    schema_version: str | None = None
    permissions: list[str] = field(default_factory=list)
    tested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "status": self.status,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "api_version": self.api_version,
            "schema_version": self.schema_version,
            "permissions": list(self.permissions),
            "tested_at": self.tested_at.isoformat(),
        }


@dataclass
class SearchPage:
    event_ids: list[str]
    next_cursor: str | None = None
    total_estimate: int | None = None
    estimate_is_exact: bool = False


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------
class DataScoutAdapter(abc.ABC):
    """Read-only interface to an event/data source."""

    name: str = "abstract"
    display_name: str = "Abstract adapter"
    is_synthetic: bool = False
    #: Adapters that require an explicit DEMO MODE opt-in set this to True.
    demo_only: bool = False

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}
        self._authenticated = False

    # -- lifecycle -------------------------------------------------------
    @abc.abstractmethod
    def authenticate(self) -> None:
        """Establish credentials. Raises on failure; never silently degrades."""

    @abc.abstractmethod
    def test_connection(self) -> ConnectionStatus:
        """Probe the source. Must not fabricate a CONNECTED result."""

    def close(self) -> None:  # noqa: B027 - optional hook, not every adapter holds resources
        """Release any held resources. File-based adapters need do nothing."""
        return None

    # -- discovery -------------------------------------------------------
    @abc.abstractmethod
    def get_projects(self) -> list[dict[str, Any]]:
        ...

    @abc.abstractmethod
    def get_datasets(self, project: str | None = None) -> list[dict[str, Any]]:
        ...

    @abc.abstractmethod
    def get_supported_filters(self, query: ScoutQuery | None = None) -> SupportedFilters:
        """Return the source's filter vocabulary, optionally narrowed by ``query``.

        Passing a partial query enables dependent filters (choose Germany ->
        only German cities and road types come back).
        """

    @abc.abstractmethod
    def get_schema(self) -> SourceSchema:
        ...

    # -- search ----------------------------------------------------------
    @abc.abstractmethod
    def search_events(self, query: ScoutQuery, *, cursor: str | None = None, limit: int = 100) -> SearchPage:
        ...

    def estimate_count(self, query: ScoutQuery) -> tuple[int | None, bool, str]:
        """Return ``(estimate, is_exact, note)``.

        A source that cannot count cheaply returns ``(None, False, reason)``
        rather than a guess.
        """
        return (None, False, "This source does not provide a record-count estimate.")

    # -- per-event retrieval ---------------------------------------------
    @abc.abstractmethod
    def get_event_metadata(self, event_id: str) -> EventMetadata:
        ...

    @abc.abstractmethod
    def get_sensor_manifest(self, event_id: str) -> list[StreamManifestEntry]:
        ...

    def get_annotations(self, event_id: str) -> list[DetectionContract]:
        """Reference annotations. Empty list + ``reference_data_available=False``
        on the bundle when the source has none."""
        return []

    def get_perception_results(self, event_id: str) -> list[DetectionContract]:
        return []

    def get_trajectory(self, event_id: str) -> list[PoseSample]:
        return []

    def get_map_context(self, event_id: str) -> MapContext:
        return MapContext(available=False, unavailable_reason="This source does not provide map context.")

    # -- composite -------------------------------------------------------
    def get_event_bundle(self, event_id: str) -> EventBundle:
        """Assemble the full bundle. Overridden by adapters that can do it in one call."""
        metadata = self.get_event_metadata(event_id)
        streams = self.get_sensor_manifest(event_id)
        poses = self.get_trajectory(event_id)
        perception = self.get_perception_results(event_id)
        reference = self.get_annotations(event_id)
        map_context = self.get_map_context(event_id)

        detections = list(perception) + list(reference)
        end_t = max((s.end_t for s in streams if s.end_t is not None), default=None)
        if end_t is None and poses:
            end_t = max(p.t for p in poses)

        return EventBundle(
            metadata=metadata,
            streams=streams,
            poses=poses,
            detections=detections,
            map_context=map_context,
            annotations_available=bool(reference),
            reference_data_available=bool(reference),
            source_end_t=end_t,
            adapter=self.name,
            is_synthetic=self.is_synthetic,
        )

    # -- introspection ---------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "is_synthetic": self.is_synthetic,
            "demo_only": self.demo_only,
            "read_only": True,
        }
