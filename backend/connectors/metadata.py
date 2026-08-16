"""Metadata resolution service.

Sits between the pipeline and the adapter so that the *confirmed* field mapping
stored on a connection profile always wins over the auto-suggested one, and so
schema discovery has a single entry point.
"""

from __future__ import annotations

from typing import Any

from backend.connectors.base import DataScoutAdapter, SourceSchema
from backend.connectors.normalization import build_event_metadata, mapping_from_schema, suggest_mapping
from backend.models.contracts import EventMetadata
from backend.models.orm import ConnectionProfile


class MetadataResolver:
    def __init__(self, adapter: DataScoutAdapter, profile: ConnectionProfile | None = None) -> None:
        self.adapter = adapter
        self.profile = profile
        self._mapping: dict[str, str] | None = None

    @property
    def confirmed_mapping(self) -> dict[str, str] | None:
        """The mapping an administrator confirmed in the mapping editor."""
        if self.profile and self.profile.field_mapping_json:
            mapping = self.profile.field_mapping_json.get("mapping")
            if isinstance(mapping, dict) and mapping:
                return {str(k): str(v) for k, v in mapping.items()}
        return None

    def mapping(self) -> dict[str, str]:
        if self._mapping is None:
            self._mapping = self.confirmed_mapping or mapping_from_schema(self.adapter.get_schema())
        return self._mapping

    def discover_schema(self) -> SourceSchema:
        """Run discovery and overlay any confirmed mapping onto the result."""
        schema = self.adapter.get_schema()
        confirmed = self.confirmed_mapping
        if confirmed:
            for descriptor in schema.fields:
                if descriptor.source_field in confirmed:
                    descriptor.canonical_field = confirmed[descriptor.source_field]
                    descriptor.mapping_method = "manual"
                    descriptor.mapping_confidence = 1.0
        return schema

    def normalize(self, raw: dict[str, Any]) -> EventMetadata:
        return build_event_metadata(raw, self.mapping())

    def metadata_for(self, event_id: str) -> EventMetadata:
        metadata = self.adapter.get_event_metadata(event_id)
        if not metadata.event_id:
            metadata.event_id = event_id
        return metadata

    @staticmethod
    def suggest_from_records(records: list[dict[str, Any]]) -> SourceSchema:
        return suggest_mapping(records)
