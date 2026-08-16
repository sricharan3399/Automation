"""Local CSV / JSON adapter.

Reads approved exported event bundles from a directory on the workstation. This
is the adapter used for development, offline testing and the golden dataset,
and it is a legitimate production integration path when the approved export
route is "Data Scout -> approved JSON/CSV export -> workstation"
(integration priority 3 in the platform spec).

Accepted layouts inside the dataset directory::

    <dir>/events/*.json      one JSON document per event (full bundle)
    <dir>/*.json             same, flat
    <dir>/*.csv              metadata-only rows, one event per row

JSON document shape (``av-scout-local-event/1.0``)::

    {
      "schema": "av-scout-local-event/1.0",
      "metadata":  { ... raw source fields, mapped via field_mapping.yaml ... },
      "streams":   [ ... ],
      "poses":     [ ... ],
      "detections":[ ... ],
      "map_context": { ... },
      "reference_data_available": true,
      "source_start_t": 0.0,
      "source_end_t": 30.0
    }

Only ``metadata`` is required. Everything else is reported as unavailable when
absent - it is never synthesised.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any

from backend.connectors.base import (
    AdapterError,
    AdapterNotConfigured,
    AdapterSchemaError,
    ConnectionStatus,
    DataScoutAdapter,
    SearchPage,
    SourceSchema,
    SupportedFilters,
)
from backend.connectors.matching import evaluate
from backend.connectors.normalization import (
    build_event_metadata,
    mapping_from_schema,
    suggest_mapping,
)
from backend.models.contracts import (
    DetectionContract,
    EventBundle,
    EventMetadata,
    MapContext,
    MapFeatureContract,
    MapGeometry,
    PoseSample,
    ScoutQuery,
    StreamManifestEntry,
    StreamSample,
)
from backend.settings import get_settings, is_fixture_dataset

log = logging.getLogger(__name__)

SCHEMA_ID = "av-scout-local-event/1.0"

# Filter vocabularies derived from the data itself, keyed by canonical field.
_FILTER_SOURCES: dict[str, str] = {
    "country_code": "country_code",
    "region": "region",
    "city": "city",
    "test_area": "test_area",
    "route": "route",
    "object_type": "object_type",
    "bus_subtype": "bus_type",
    "road_type": "road_type",
    "intersection_type": "intersection_type",
    "intersection_complexity": "intersection_complexity",
    "traffic_control_entity": "traffic_control_entity",
    "traffic_light_state": "traffic_light_state",
    "vehicle_maneuver": "vehicle_maneuver",
    "weather": "weather",
    "lighting": "lighting",
    "bus_scenario_tag": "scenario_tags",
    "project": "project",
    "dataset": "dataset",
    "dataset_version": "dataset_version",
    "drive_collection": "drive_collection",
    "vehicle_build": "vehicle_build",
    "software_version": "software_version",
    "map_version": "map_version",
}


class LocalFilesAdapter(DataScoutAdapter):
    name = "local_files"
    display_name = "Local CSV / JSON Dataset"
    is_synthetic = False

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        super().__init__(settings)
        configured = (self.settings or {}).get("dataset_dir")
        # May legitimately be None: "no dataset configured" is a real state and
        # must reach authenticate() intact rather than silently resolving to
        # some fallback directory.
        self.dataset_dir: Path | None = (
            Path(configured) if configured else get_settings().local_dataset_dir
        )
        # Value is the backing file, or None for in-memory subclasses.
        self._index: dict[str, Any] | None = None
        self._raw_cache: dict[str, dict[str, Any]] = {}
        self._mapping: dict[str, str] | None = None

    # -- lifecycle -------------------------------------------------------
    @property
    def serves_fixture_data(self) -> bool:
        """True when this adapter is pointed at the committed test fixtures.

        Reading the fixtures is allowed - the test suite and CI both do it on
        purpose - but the results are synthetic, so every surface that reports
        on this connection has to be able to say so. Silence here is what let
        golden-dataset events be presented as production results.
        """
        return is_fixture_dataset(self.dataset_dir)

    def authenticate(self) -> None:
        if self.dataset_dir is None:
            raise AdapterNotConfigured(
                "no dataset directory configured",
                user_message=(
                    "No local dataset directory is configured.\n\n"
                    "Set one on the Connections page, or point AV_LOCAL_DATASET_DIR at an "
                    "approved exported dataset. The platform does not fall back to sample "
                    "data when a source is unconfigured."
                ),
            )
        if not self.dataset_dir.is_dir():
            raise AdapterNotConfigured(
                f"dataset directory not found: {self.dataset_dir}",
                user_message=(
                    f"The local dataset directory does not exist:\n{self.dataset_dir}\n\n"
                    "Set it on the Connections page, or point AV_LOCAL_DATASET_DIR at an "
                    "approved exported dataset."
                ),
            )
        self._authenticated = True

    def test_connection(self) -> ConnectionStatus:
        started = time.perf_counter()
        try:
            self.authenticate()
            index = self._build_index()
        except AdapterError as exc:
            return ConnectionStatus(
                connected=False,
                status="NOT_CONFIGURED",
                message=exc.user_message,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        latency = (time.perf_counter() - started) * 1000.0
        if not index:
            return ConnectionStatus(
                connected=False,
                status="ERROR",
                message=(
                    f"No event documents found in {self.dataset_dir}. Expected *.json bundles "
                    "or a *.csv metadata export."
                ),
                latency_ms=latency,
            )
        message = f"{len(index)} events available in {self.dataset_dir}"
        if self.serves_fixture_data:
            # Never let a fixture-backed connection read as a production source.
            message = (
                f"FIXTURE DATA - NOT REAL AV DATA. {message}\n\n"
                "This connection is pointed at the repository's committed test "
                "fixtures. Results derived from it are synthetic and must not be "
                "treated as production findings. Production Readiness will not "
                "pass while a fixture dataset is the active event source."
            )
        return ConnectionStatus(
            connected=True,
            status="CONNECTED",
            message=message,
            latency_ms=latency,
            api_version="local-fs",
            schema_version=SCHEMA_ID,
            permissions=["read"],
        )

    # -- index -----------------------------------------------------------
    def _build_index(self) -> dict[str, Any]:
        if self._index is not None:
            return self._index

        if self.dataset_dir is None or not self.dataset_dir.is_dir():
            raise AdapterNotConfigured(f"dataset directory not found: {self.dataset_dir}")

        index: dict[str, Any] = {}
        # An `events/` subdirectory, when present, is authoritative: sibling
        # files such as a dataset manifest must not be read as events.
        events_dir = self.dataset_dir / "events"
        if events_dir.is_dir():
            json_paths = sorted(events_dir.glob("*.json"))
        else:
            json_paths = sorted(self.dataset_dir.glob("*.json"))
        for path in json_paths:
            if path.name.startswith("_") or path.name in {"manifest.json", "index.json"}:
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                log.warning("Skipping unreadable JSON event %s: %s", path.name, exc)
                continue
            if not isinstance(document, dict):
                continue
            raw = document.get("metadata", document)
            event_id = str(raw.get("event_id") or raw.get("event") or path.stem)
            index[event_id] = path
            self._raw_cache[event_id] = document

        for path in sorted(self.dataset_dir.glob("*.csv")):
            if path.name.startswith("_") or path.name == "rejected_records.csv":
                continue
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        raw = {k: v for k, v in row.items() if k}
                        event_id = str(raw.get("event_id") or raw.get("event") or "").strip()
                        if not event_id or event_id in index:
                            continue
                        index[event_id] = path
                        self._raw_cache[event_id] = {"metadata": raw}
            except (UnicodeDecodeError, csv.Error) as exc:
                log.warning("Skipping unreadable CSV %s: %s", path.name, exc)

        self._index = index
        return index

    def _document(self, event_id: str) -> dict[str, Any]:
        self._build_index()
        document = self._raw_cache.get(event_id)
        if document is None:
            raise AdapterSchemaError(
                f"unknown event id: {event_id}",
                user_message=f"Event '{event_id}' is not present in {self.dataset_dir}.",
            )
        return document

    def _raw_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        self._build_index()
        records = [doc.get("metadata", doc) for doc in self._raw_cache.values()]
        return records[:limit] if limit else records

    def _field_mapping(self) -> dict[str, str]:
        if self._mapping is None:
            self._mapping = mapping_from_schema(self.get_schema())
        return self._mapping

    # -- discovery -------------------------------------------------------
    def get_schema(self) -> SourceSchema:
        schema = suggest_mapping(self._raw_records(limit=25))
        schema.api_version = "local-fs"
        schema.schema_version = SCHEMA_ID
        return schema

    def get_projects(self) -> list[dict[str, Any]]:
        seen: dict[str, int] = {}
        for record in self._raw_records():
            name = str(record.get("project") or "").strip()
            if name:
                seen[name] = seen.get(name, 0) + 1
        return [{"id": name, "name": name, "event_count": count} for name, count in sorted(seen.items())]

    def get_datasets(self, project: str | None = None) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str], int] = {}
        for record in self._raw_records():
            if project and str(record.get("project") or "") != project:
                continue
            name = str(record.get("dataset") or "").strip()
            version = str(record.get("dataset_version") or "").strip()
            if name:
                seen[(name, version)] = seen.get((name, version), 0) + 1
        return [
            {"id": f"{name}:{version}" if version else name, "name": name, "version": version, "event_count": n}
            for (name, version), n in sorted(seen.items())
        ]

    def get_supported_filters(self, query: ScoutQuery | None = None) -> SupportedFilters:
        """Vocabulary derived from the data actually present.

        When ``query`` is supplied the vocabulary is narrowed to events that
        already match it, which is what makes dependent filters work (choose
        Germany -> only German cities are offered).
        """
        mapping = self._field_mapping()
        values: dict[str, set[str]] = {key: set() for key in _FILTER_SOURCES}

        for event_id in self._build_index():
            metadata = self._metadata_for(event_id, mapping)
            if query is not None and not evaluate(query, metadata)[0]:
                continue
            for filter_key, attribute in _FILTER_SOURCES.items():
                value = getattr(metadata, attribute, None)
                if value is None:
                    continue
                if isinstance(value, list):
                    values[filter_key].update(str(v) for v in value if v)
                elif str(value).strip():
                    values[filter_key].add(str(value).strip())

        return SupportedFilters(
            values={k: sorted(v) for k, v in values.items() if v},
            origin="source",
            supports_count_estimate=True,
            supports_pagination=True,
            note="Values derived from the events present in the local dataset.",
        )

    # -- search ----------------------------------------------------------
    def _metadata_for(self, event_id: str, mapping: dict[str, str] | None = None) -> EventMetadata:
        document = self._document(event_id)
        raw = document.get("metadata", document)
        metadata = build_event_metadata(raw, mapping or self._field_mapping())
        if not metadata.event_id:
            metadata.event_id = event_id
        return metadata

    def search_events(self, query: ScoutQuery, *, cursor: str | None = None, limit: int = 100) -> SearchPage:
        mapping = self._field_mapping()
        all_ids = sorted(self._build_index())
        matched = [eid for eid in all_ids if evaluate(query, self._metadata_for(eid, mapping))[0]]

        start = 0
        if cursor:
            try:
                start = max(0, int(cursor))
            except ValueError:
                start = 0
        page = matched[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(matched) else None
        return SearchPage(
            event_ids=page,
            next_cursor=next_cursor,
            total_estimate=len(matched),
            estimate_is_exact=True,
        )

    def estimate_count(self, query: ScoutQuery) -> tuple[int | None, bool, str]:
        mapping = self._field_mapping()
        count = sum(1 for eid in self._build_index() if evaluate(query, self._metadata_for(eid, mapping))[0])
        return count, True, "Exact count from the local dataset index."

    # -- per-event -------------------------------------------------------
    def get_event_metadata(self, event_id: str) -> EventMetadata:
        return self._metadata_for(event_id)

    def get_sensor_manifest(self, event_id: str) -> list[StreamManifestEntry]:
        document = self._document(event_id)
        entries: list[StreamManifestEntry] = []
        for raw in document.get("streams", []) or []:
            if not isinstance(raw, dict):
                continue
            samples = [
                StreamSample(
                    t=float(s.get("t", 0.0)),
                    signature=s.get("signature"),
                    payload=s.get("payload", {}) or {},
                )
                for s in raw.get("samples", []) or []
                if isinstance(s, dict)
            ]
            entries.append(
                StreamManifestEntry(
                    stream_type=str(raw.get("stream_type", "unknown")),
                    camera_position=raw.get("camera_position"),
                    present=bool(raw.get("present", True)),
                    start_t=raw.get("start_t"),
                    end_t=raw.get("end_t"),
                    nominal_rate_hz=raw.get("nominal_rate_hz"),
                    sample_count=int(raw.get("sample_count", len(samples))),
                    samples=samples,
                    declared_offset_ms=raw.get("declared_offset_ms"),
                    uri=raw.get("uri"),
                    notes=raw.get("notes"),
                )
            )
        return entries

    def get_trajectory(self, event_id: str) -> list[PoseSample]:
        document = self._document(event_id)
        return [
            PoseSample(
                t=float(p["t"]),
                x_m=float(p["x_m"]),
                y_m=float(p["y_m"]),
                heading_rad=p.get("heading_rad"),
                speed_mps=p.get("speed_mps"),
                accel_mps2=p.get("accel_mps2"),
                steering_rad=p.get("steering_rad"),
                localization_quality=p.get("localization_quality"),
            )
            for p in document.get("poses", []) or []
            if isinstance(p, dict) and {"t", "x_m", "y_m"} <= set(p)
        ]

    def _detections(self, event_id: str, source: str) -> list[DetectionContract]:
        document = self._document(event_id)
        out: list[DetectionContract] = []
        for raw in document.get("detections", []) or []:
            if not isinstance(raw, dict) or raw.get("source", "perception") != source:
                continue
            out.append(
                DetectionContract(
                    t=float(raw.get("t", 0.0)),
                    camera=raw.get("camera"),
                    source=source,  # type: ignore[arg-type]
                    object_type=str(raw.get("object_type", "unknown")),
                    object_subtype=raw.get("object_subtype"),
                    track_id=raw.get("track_id"),
                    bounding_box=raw.get("bounding_box", {}) or {},
                    state=raw.get("state"),
                    distance_m=raw.get("distance_m"),
                    velocity_mps=raw.get("velocity_mps"),
                    lane_relation=raw.get("lane_relation"),
                    confidence=raw.get("confidence"),
                    model_version=raw.get("model_version"),
                )
            )
        return out

    def get_perception_results(self, event_id: str) -> list[DetectionContract]:
        return self._detections(event_id, "perception")

    def get_annotations(self, event_id: str) -> list[DetectionContract]:
        return self._detections(event_id, "reference")

    def get_map_context(self, event_id: str) -> MapContext:
        document = self._document(event_id)
        raw = document.get("map_context")
        if not isinstance(raw, dict) or not raw.get("features"):
            return MapContext(
                available=False,
                unavailable_reason="The local event document contains no map_context section.",
            )
        features: list[MapFeatureContract] = []
        for item in raw.get("features", []):
            if not isinstance(item, dict) or "geometry" not in item:
                continue
            geometry = item["geometry"]
            try:
                features.append(
                    MapFeatureContract(
                        feature_id=str(item.get("feature_id", "")),
                        feature_type=str(item.get("feature_type", "unknown")),
                        geometry=MapGeometry(
                            type=geometry["type"], coordinates=geometry["coordinates"]
                        ),
                        attributes=item.get("attributes", {}) or {},
                        map_version=item.get("map_version") or raw.get("map_version"),
                        confidence=item.get("confidence"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping malformed map feature in %s: %s", event_id, exc)
        return MapContext(
            available=bool(features),
            map_version=raw.get("map_version"),
            features=features,
            unavailable_reason=None if features else "No usable map features in the document.",
        )

    # -- composite -------------------------------------------------------
    def get_event_bundle(self, event_id: str) -> EventBundle:
        document = self._document(event_id)
        reference = self.get_annotations(event_id)
        streams = self.get_sensor_manifest(event_id)
        poses = self.get_trajectory(event_id)

        end_t = document.get("source_end_t")
        if end_t is None:
            candidates = [s.end_t for s in streams if s.end_t is not None]
            if poses:
                candidates.append(max(p.t for p in poses))
            end_t = max(candidates) if candidates else None

        return EventBundle(
            metadata=self.get_event_metadata(event_id),
            streams=streams,
            poses=poses,
            detections=self.get_perception_results(event_id) + reference,
            map_context=self.get_map_context(event_id),
            annotations_available=bool(reference),
            reference_data_available=bool(document.get("reference_data_available", bool(reference))),
            source_start_t=float(document.get("source_start_t", 0.0)),
            source_end_t=end_t,
            adapter=self.name,
            is_synthetic=bool(document.get("is_synthetic", False)),
        )
