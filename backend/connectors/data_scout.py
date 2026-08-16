"""NVIDIA / internal Data Scout adapter.

**No NVIDIA-proprietary endpoint, payload shape or authentication flow is
invented in this file.** None has been supplied, so none is guessed.

What this adapter provides is a complete, generic, configurable REST client:
the base URL, authentication mode, endpoint paths, pagination style and
response field paths are all supplied by an administrator on the Connections
page and stored in the connection profile. When the approved interface details
arrive, they are entered as configuration - or, if the real interface is an SDK
/ GraphQL / CLI, a sibling adapter is added behind the same
:class:`DataScoutAdapter` interface and nothing above this layer changes.

Until then every method raises :class:`AdapterNotConfigured`, and
``test_connection`` reports ``NOT_CONFIGURED``. The adapter never reports a
healthy connection it does not have, and never returns fabricated events.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.auth.secrets import SecretNotAvailable, resolve_secret
from backend.connectors.base import (
    AdapterAuthError,
    AdapterNotConfigured,
    AdapterPermissionError,
    AdapterRateLimited,
    AdapterSchemaError,
    AdapterUnavailable,
    ConnectionStatus,
    DataScoutAdapter,
    SearchPage,
    SourceSchema,
    SupportedFilters,
)
from backend.connectors.normalization import build_event_metadata, mapping_from_schema, suggest_mapping
from backend.models.contracts import (
    DetectionContract,
    EventMetadata,
    MapContext,
    PoseSample,
    ScoutQuery,
    StreamManifestEntry,
)
from backend.settings import get_settings

log = logging.getLogger(__name__)

#: Endpoint paths are configuration, not assumptions. Empty until supplied.
REQUIRED_ENDPOINTS = ["search_events", "event_metadata"]
OPTIONAL_ENDPOINTS = [
    "projects",
    "datasets",
    "supported_filters",
    "schema",
    "sensor_manifest",
    "annotations",
    "perception_results",
    "trajectory",
    "map_context",
    "count",
    "health",
]

MISSING_CONFIG_MESSAGE = """\
The NVIDIA / Internal Data Scout adapter is NOT CONFIGURED.

The platform will not simulate this connection. To enable it, an administrator
must supply the approved values on the Connections page:

  1. Base URL of the approved Data Scout API
  2. Authentication mode (bearer / api_key / oauth_client_credentials) and the
     credential-store key holding the secret
  3. Endpoint paths for at least: search_events, event_metadata
  4. Response field paths (where the event list and cursor live in a response)
  5. The integration type, if it is not REST (SDK / GraphQL / CLI / export)

Until then, use the Local CSV / JSON adapter for approved exported data, or
switch to DEMO MODE for a synthetic walkthrough.\
"""


def _dig(payload: Any, path: str | None) -> Any:
    """Fetch a dotted path out of a response body. ``None`` path returns payload."""
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


class NvidiaInternalDataScoutAdapter(DataScoutAdapter):
    name = "nvidia_internal_data_scout"
    display_name = "NVIDIA / Internal Data Scout"
    is_synthetic = False

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        super().__init__(settings)
        app = get_settings()
        cfg = self.settings or {}

        self.base_url: str | None = cfg.get("base_url") or app.data_scout.base_url
        self.auth_mode: str = cfg.get("auth_mode") or app.data_scout.auth_mode
        self.integration_type: str = cfg.get("integration_type") or app.data_scout.integration_type
        self.verify_tls: bool = bool(cfg.get("verify_tls", app.data_scout.verify_tls))
        self.timeout: float = float(cfg.get("timeout_seconds", app.data_scout.timeout_seconds))
        self.credential_key: str = cfg.get("credential_key") or app.data_scout.token_env_var

        self.endpoints: dict[str, str] = dict(cfg.get("endpoints", {}) or {})
        self.response_paths: dict[str, str] = dict(cfg.get("response_paths", {}) or {})
        self.query_translation: dict[str, str] = dict(cfg.get("query_translation", {}) or {})
        self.page_param: str = cfg.get("page_param", "cursor")
        self.limit_param: str = cfg.get("limit_param", "limit")

        self._client: httpx.Client | None = None
        self._mapping: dict[str, str] | None = None
        self._enabled = bool(cfg.get("enabled", app.data_scout.enabled))

    # -- configuration state ---------------------------------------------
    def missing_configuration(self) -> list[str]:
        missing: list[str] = []
        if not self._enabled:
            missing.append("adapter is disabled")
        if not self.base_url:
            missing.append("base_url")
        for key in REQUIRED_ENDPOINTS:
            if not self.endpoints.get(key):
                missing.append(f"endpoints.{key}")
        if self.auth_mode not in ("none", "bearer", "api_key", "oauth_client_credentials"):
            missing.append(f"unsupported auth_mode '{self.auth_mode}'")
        if self.integration_type != "rest_api":
            missing.append(
                f"integration_type '{self.integration_type}' requires a dedicated adapter "
                "(this class implements REST only)"
            )
        return missing

    @property
    def is_configured(self) -> bool:
        return not self.missing_configuration()

    def _require_configured(self) -> None:
        missing = self.missing_configuration()
        if missing:
            raise AdapterNotConfigured(
                "data scout adapter not configured: " + ", ".join(missing),
                user_message=MISSING_CONFIG_MESSAGE,
                detail={"missing": missing},
            )

    # -- lifecycle -------------------------------------------------------
    def authenticate(self) -> None:
        self._require_configured()
        headers = {"Accept": "application/json", "User-Agent": f"av-test-automation/{get_settings().software_version}"}

        if self.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self._secret()}"
        elif self.auth_mode == "api_key":
            headers[self.settings.get("api_key_header", "X-API-Key")] = self._secret()
        elif self.auth_mode == "oauth_client_credentials":
            raise AdapterNotConfigured(
                "oauth client-credentials flow requires the approved token endpoint",
                user_message=(
                    "OAuth client-credentials is selected but the approved token endpoint, "
                    "client id and scope have not been supplied. Add them on the Connections page."
                ),
            )

        self._client = httpx.Client(
            base_url=self.base_url or "",
            headers=headers,
            timeout=self.timeout,
            verify=self.verify_tls,
            follow_redirects=False,
        )
        self._authenticated = True

    def _secret(self) -> str:
        try:
            value = resolve_secret(self.credential_key)
        except SecretNotAvailable as exc:
            raise AdapterAuthError(
                str(exc),
                user_message=(
                    f"No credential found for '{self.credential_key}'. Store it in the OS credential "
                    "store or inject it through the approved secret manager. Secrets are never saved "
                    "into configuration profiles."
                ),
            ) from exc
        assert value is not None
        return value

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._authenticated = False

    def _http(self) -> httpx.Client:
        if self._client is None:
            self.authenticate()
        assert self._client is not None
        return self._client

    @retry(
        retry=retry_if_exception_type((AdapterUnavailable, httpx.TransportError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _request(self, endpoint_key: str, *, params: dict[str, Any] | None = None, **fmt: Any) -> Any:
        path_template = self.endpoints.get(endpoint_key)
        if not path_template:
            raise AdapterNotConfigured(
                f"endpoint '{endpoint_key}' is not configured",
                user_message=(
                    f"This action needs the '{endpoint_key}' endpoint path, which has not been "
                    "supplied for the Data Scout connection."
                ),
            )
        path = path_template.format(**fmt)
        try:
            response = self._http().get(path, params=params or None)
        except httpx.TimeoutException as exc:
            raise AdapterUnavailable(
                f"timeout calling {path}",
                user_message="Data Scout did not respond in time. Progress was checkpointed.",
            ) from exc
        except httpx.TransportError as exc:
            raise AdapterUnavailable(
                f"transport error calling {path}: {exc}",
                user_message="Data Scout could not be reached. Check the VPN / corporate network connection.",
            ) from exc

        return self._handle_response(response, path)

    @staticmethod
    def _handle_response(response: httpx.Response, path: str) -> Any:
        code = response.status_code
        if code == 401:
            raise AdapterAuthError(
                f"401 from {path}",
                user_message="Data Scout rejected the credentials. They may have expired.",
            )
        if code == 403:
            raise AdapterPermissionError(
                f"403 from {path}",
                user_message="Your account is not permitted to access this dataset in Data Scout.",
            )
        if code == 404:
            raise AdapterSchemaError(
                f"404 from {path}",
                user_message=(
                    f"Data Scout has no endpoint at '{path}'. Correct the endpoint path on the "
                    "Connections page."
                ),
            )
        if code == 429:
            raise AdapterRateLimited(f"429 from {path}")
        if 500 <= code < 600:
            raise AdapterUnavailable(
                f"{code} from {path}",
                user_message=(
                    f"Data Scout is temporarily unavailable (HTTP {code}). Progress was checkpointed; "
                    "you can retry or resume later."
                ),
            )
        if code >= 400:
            raise AdapterSchemaError(f"{code} from {path}", user_message=f"Data Scout rejected the request (HTTP {code}).")

        try:
            return response.json()
        except ValueError as exc:
            raise AdapterSchemaError(
                f"non-JSON response from {path}",
                user_message="Data Scout returned a response this version cannot parse.",
            ) from exc

    def test_connection(self) -> ConnectionStatus:
        missing = self.missing_configuration()
        if missing:
            return ConnectionStatus(
                connected=False,
                status="NOT_CONFIGURED",
                message=MISSING_CONFIG_MESSAGE,
                permissions=[],
            )
        started = time.perf_counter()
        try:
            payload = self._request("health" if self.endpoints.get("health") else "search_events", params={self.limit_param: 1})
        except AdapterAuthError as exc:
            return ConnectionStatus(connected=False, status="AUTH_FAILED", message=exc.user_message)
        except AdapterNotConfigured as exc:
            return ConnectionStatus(connected=False, status="NOT_CONFIGURED", message=exc.user_message)
        except (AdapterUnavailable, AdapterSchemaError, AdapterPermissionError) as exc:
            return ConnectionStatus(connected=False, status="ERROR", message=exc.user_message)

        latency = (time.perf_counter() - started) * 1000.0
        return ConnectionStatus(
            connected=True,
            status="CONNECTED",
            message="Data Scout responded successfully.",
            latency_ms=latency,
            api_version=str(_dig(payload, self.response_paths.get("api_version")) or "unknown"),
            schema_version=str(_dig(payload, self.response_paths.get("schema_version")) or "unknown"),
            permissions=[str(p) for p in (_dig(payload, self.response_paths.get("permissions")) or [])],
        )

    # -- discovery -------------------------------------------------------
    def get_projects(self) -> list[dict[str, Any]]:
        self._require_configured()
        payload = self._request("projects")
        items = _dig(payload, self.response_paths.get("projects")) or payload
        return list(items) if isinstance(items, list) else []

    def get_datasets(self, project: str | None = None) -> list[dict[str, Any]]:
        self._require_configured()
        params = {"project": project} if project else None
        payload = self._request("datasets", params=params)
        items = _dig(payload, self.response_paths.get("datasets")) or payload
        return list(items) if isinstance(items, list) else []

    def get_supported_filters(self, query: ScoutQuery | None = None) -> SupportedFilters:
        """Ask the source for its own vocabulary.

        When the source cannot describe itself the caller receives
        ``origin='fallback'`` and the bundled taxonomy is used instead - clearly
        labelled in the UI, never presented as source-derived.
        """
        self._require_configured()
        if not self.endpoints.get("supported_filters"):
            return SupportedFilters(
                values={},
                origin="fallback",
                note="Data Scout has no supported_filters endpoint configured; using the bundled taxonomy.",
            )
        params = self.translate_query(query) if query else None
        payload = self._request("supported_filters", params=params)
        values = _dig(payload, self.response_paths.get("supported_filters")) or payload
        if not isinstance(values, dict):
            raise AdapterSchemaError("supported_filters did not return an object")
        return SupportedFilters(
            values={str(k): [str(x) for x in v] for k, v in values.items() if isinstance(v, list)},
            origin="source",
            supports_count_estimate=bool(self.endpoints.get("count")),
        )

    def get_schema(self) -> SourceSchema:
        self._require_configured()
        if self.endpoints.get("schema"):
            payload = self._request("schema")
            fields = _dig(payload, self.response_paths.get("schema")) or payload
            if isinstance(fields, dict):
                schema = suggest_mapping([fields])
                schema.note = "Derived from the Data Scout schema endpoint."
                return schema
        # Fall back to inferring the schema from a small sample of real records.
        page = self.search_events(ScoutQuery(), limit=5)
        records = [self._raw_metadata(eid) for eid in page.event_ids[:5]]
        schema = suggest_mapping(records)
        schema.note = "Inferred from a sample of Data Scout records (no schema endpoint configured)."
        return schema

    # -- query translation -----------------------------------------------
    def translate_query(self, query: ScoutQuery) -> dict[str, Any]:
        """Translate dashboard filters into the source's own query parameters.

        ``query_translation`` maps canonical filter names to the source's
        parameter names; unmapped filters are omitted from the native query and
        applied by the platform's post-filter instead, so a filter is never
        silently dropped.
        """
        out: dict[str, Any] = {}
        candidates: dict[str, Any] = {
            "country_code": query.country_code,
            "region": query.regions,
            "city": query.cities,
            "test_area": query.test_areas,
            "route": query.routes,
            "object_type": query.object_types,
            "bus_type": query.bus_subtypes,
            "scenario_tags": query.scenario_tags,
            "road_type": query.road_types,
            "lane_count": query.lanes.lane_count_exact,
            "min_lanes": query.lanes.min_lanes,
            "max_lanes": query.lanes.max_lanes,
            "intersection_type": query.intersection_types,
            "traffic_control_entity": query.traffic_control_entities,
            "traffic_light_state": query.traffic_light_states,
            "vehicle_maneuver": query.vehicle_maneuvers,
            "weather": query.weather,
            "lighting": query.lighting,
            "start_date": query.time_range.start_date,
            "end_date": query.time_range.end_date,
            "project": query.dataset.project,
            "dataset": query.dataset.dataset,
            "dataset_version": query.dataset.dataset_version,
            "software_version": query.dataset.software_version,
            "map_version": query.dataset.map_version,
        }
        for canonical_name, value in candidates.items():
            if value in (None, "", [], {}):
                continue
            source_param = self.query_translation.get(canonical_name)
            if not source_param:
                continue
            out[source_param] = ",".join(str(v) for v in value) if isinstance(value, list) else value
        return out

    def untranslated_filters(self, query: ScoutQuery) -> list[str]:
        """Filters the source cannot express natively (applied locally instead)."""
        active = []
        if query.country_code and not self.query_translation.get("country_code"):
            active.append("country_code")
        for name, value in (
            ("object_type", query.object_types),
            ("road_type", query.road_types),
            ("intersection_type", query.intersection_types),
            ("weather", query.weather),
            ("lighting", query.lighting),
            ("scenario_tags", query.scenario_tags),
        ):
            if value and not self.query_translation.get(name):
                active.append(name)
        return active

    # -- search ----------------------------------------------------------
    def search_events(self, query: ScoutQuery, *, cursor: str | None = None, limit: int = 100) -> SearchPage:
        self._require_configured()
        params = self.translate_query(query)
        params[self.limit_param] = limit
        if cursor:
            params[self.page_param] = cursor

        payload = self._request("search_events", params=params)
        items = _dig(payload, self.response_paths.get("event_list"))
        if items is None:
            items = payload if isinstance(payload, list) else []
        if not isinstance(items, list):
            raise AdapterSchemaError(
                "event list path did not resolve to a list",
                user_message=(
                    "Data Scout's response does not contain an event list at the configured path. "
                    "Check 'response_paths.event_list' on the Connections page."
                ),
            )

        id_field = self.response_paths.get("event_id_field", "event_id")
        event_ids: list[str] = []
        for item in items:
            if isinstance(item, dict):
                value = _dig(item, id_field)
                if value:
                    event_ids.append(str(value))
            elif item:
                event_ids.append(str(item))

        next_cursor = _dig(payload, self.response_paths.get("next_cursor"))
        total = _dig(payload, self.response_paths.get("total_count"))
        return SearchPage(
            event_ids=event_ids,
            next_cursor=str(next_cursor) if next_cursor else None,
            total_estimate=int(total) if isinstance(total, (int, float)) else None,
            estimate_is_exact=bool(self.response_paths.get("total_count")),
        )

    def estimate_count(self, query: ScoutQuery) -> tuple[int | None, bool, str]:
        self._require_configured()
        if self.endpoints.get("count"):
            payload = self._request("count", params=self.translate_query(query))
            value = _dig(payload, self.response_paths.get("count")) or payload
            if isinstance(value, (int, float)):
                return int(value), True, "Exact count reported by Data Scout."
        page = self.search_events(query, limit=1)
        if page.total_estimate is not None:
            return page.total_estimate, page.estimate_is_exact, "Total reported by the search endpoint."
        return None, False, "Data Scout did not report a total; the run will page until exhausted."

    # -- per-event -------------------------------------------------------
    def _raw_metadata(self, event_id: str) -> dict[str, Any]:
        payload = self._request("event_metadata", event_id=event_id)
        record = _dig(payload, self.response_paths.get("event_metadata")) or payload
        if not isinstance(record, dict):
            raise AdapterSchemaError(f"event metadata for {event_id} is not an object")
        return record

    def _mapping_for_source(self) -> dict[str, str]:
        if self._mapping is None:
            configured = self.settings.get("field_mapping")
            self._mapping = dict(configured) if configured else mapping_from_schema(self.get_schema())
        return self._mapping

    def get_event_metadata(self, event_id: str) -> EventMetadata:
        self._require_configured()
        metadata = build_event_metadata(self._raw_metadata(event_id), self._mapping_for_source())
        if not metadata.event_id:
            metadata.event_id = event_id
        return metadata

    def get_sensor_manifest(self, event_id: str) -> list[StreamManifestEntry]:
        self._require_configured()
        if not self.endpoints.get("sensor_manifest"):
            return []
        payload = self._request("sensor_manifest", event_id=event_id)
        items = _dig(payload, self.response_paths.get("sensor_manifest")) or payload
        if not isinstance(items, list):
            raise AdapterSchemaError(f"sensor manifest for {event_id} is not a list")
        return [StreamManifestEntry(**item) for item in items if isinstance(item, dict)]

    def _detections(self, endpoint_key: str, event_id: str, source: str) -> list[DetectionContract]:
        if not self.endpoints.get(endpoint_key):
            return []
        payload = self._request(endpoint_key, event_id=event_id)
        items = _dig(payload, self.response_paths.get(endpoint_key)) or payload
        if not isinstance(items, list):
            return []
        out: list[DetectionContract] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item.setdefault("source", source)
            try:
                out.append(DetectionContract(**item))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed detection for %s: %s", event_id, exc)
        return out

    def get_annotations(self, event_id: str) -> list[DetectionContract]:
        self._require_configured()
        return self._detections("annotations", event_id, "reference")

    def get_perception_results(self, event_id: str) -> list[DetectionContract]:
        self._require_configured()
        return self._detections("perception_results", event_id, "perception")

    def get_trajectory(self, event_id: str) -> list[PoseSample]:
        self._require_configured()
        if not self.endpoints.get("trajectory"):
            return []
        payload = self._request("trajectory", event_id=event_id)
        items = _dig(payload, self.response_paths.get("trajectory")) or payload
        if not isinstance(items, list):
            return []
        out: list[PoseSample] = []
        for item in items:
            if isinstance(item, dict) and {"t", "x_m", "y_m"} <= set(item):
                out.append(PoseSample(**item))
        return out

    def get_map_context(self, event_id: str) -> MapContext:
        self._require_configured()
        if not self.endpoints.get("map_context"):
            return MapContext(
                available=False,
                unavailable_reason="No map_context endpoint is configured for this Data Scout connection.",
            )
        payload = self._request("map_context", event_id=event_id)
        record = _dig(payload, self.response_paths.get("map_context")) or payload
        if not isinstance(record, dict):
            return MapContext(available=False, unavailable_reason="Map context response was not an object.")
        try:
            return MapContext(**record)
        except (TypeError, ValueError) as exc:
            raise AdapterSchemaError(
                f"map context for {event_id} did not match the contract: {exc}",
                user_message="Data Scout's map context does not match the expected structure.",
            ) from exc
