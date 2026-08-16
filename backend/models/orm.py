"""SQLAlchemy ORM entities.

Portability: every column type used here works on both SQLite (default, zero
administration) and PostgreSQL. Geometry is stored as GeoJSON-shaped JSON so a
SQLite deployment needs no spatial extension; a PostGIS deployment can add a
generated ``geography`` column and spatial indexes via migration without
changing application code (see docs/DEPLOYMENT.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
class AutomationRun(Base):
    """One execution of the automation pipeline."""

    __tablename__ = "automation_runs"

    run_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    # PENDING | VALIDATING | RUNNING | PAUSED | CANCELLED | COMPLETED | FAILED
    stage: Mapped[str] = mapped_column(String(64), default="not_started")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    # Frozen configuration for reproducibility (spec 93).
    profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frozen_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    connection_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adapter_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    software_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    contract_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    rule_version: Mapped[str] = mapped_column(String(64), default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    map_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Counters surfaced on the Home dashboard and the Run page.
    records_discovered: Mapped[int] = mapped_column(Integer, default=0)
    records_scanned: Mapped[int] = mapped_column(Integer, default=0)
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_matched_country: Mapped[int] = mapped_column(Integer, default=0)
    records_matched_scenario: Mapped[int] = mapped_column(Integer, default=0)
    candidate_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    blocking_error_count: Mapped[int] = mapped_column(Integer, default=0)
    review_required_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_merged: Mapped[int] = mapped_column(Integer, default=0)
    csv_rows_created: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    completed_stages: Mapped[list[str]] = mapped_column(JSON, default=list)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_dir: Mapped[str | None] = mapped_column(String(512), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(128), default="local.tester")

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or utcnow()
        start = self.started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0.0, (end - start).total_seconds())


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class Event(Base):
    """A canonical event record.

    Identity is the ``canonical_event_key``: re-processing the same source
    event UPSERTS this row (bumping ``record_version``) instead of inserting a
    duplicate. Reviewer decisions live in :class:`Review` and are never
    overwritten by re-processing.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_country_status", "country_code", "status"),
        Index("ix_events_run", "last_run_pk"),
    )

    event_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_event_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Anonymised references are what leaves the approved environment.
    anonymized_job_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anonymized_session_ref: Mapped[str] = mapped_column(String(64), default="")
    anonymized_event_ref: Mapped[str] = mapped_column(String(64), default="", index=True)

    # Source identifiers stay inside the approved environment only.
    source_event_id: Mapped[str] = mapped_column(String(256), default="")
    source_session_id: Mapped[str] = mapped_column(String(256), default="")

    event_type: Mapped[str] = mapped_column(String(64), default="unknown")
    approx_event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluation_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluation_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    country_source_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)

    road_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lane_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lane_relation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intersection_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intersection_complexity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lighting: Mapped[str | None] = mapped_column(String(32), nullable=True)

    object_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    bus_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scenario_tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Full normalised metadata and derived analysis payloads (versioned JSON).
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE", index=True)
    record_version: Mapped[int] = mapped_column(Integer, default=1)

    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    blocking_error_count: Mapped[int] = mapped_column(Integer, default=0)
    review_required: Mapped[bool] = mapped_column(Boolean, default=True)

    first_run_pk: Mapped[int | None] = mapped_column(ForeignKey("automation_runs.run_pk"), nullable=True)
    last_run_pk: Mapped[int | None] = mapped_column(ForeignKey("automation_runs.run_pk"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    streams: Mapped[list[SensorStream]] = relationship(back_populates="event", cascade="all, delete-orphan")
    poses: Mapped[list[EgoPose]] = relationship(back_populates="event", cascade="all, delete-orphan")
    map_features: Mapped[list[MapFeature]] = relationship(back_populates="event", cascade="all, delete-orphan")
    detections: Mapped[list[Detection]] = relationship(back_populates="event", cascade="all, delete-orphan")
    recommendations: Mapped[list[FieldRecommendation]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    validations: Mapped[list[ValidationResult]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    evidence: Mapped[list[Evidence]] = relationship(back_populates="event", cascade="all, delete-orphan")
    reviews: Mapped[list[Review]] = relationship(back_populates="event", cascade="all, delete-orphan")


class SensorStream(Base):
    __tablename__ = "sensor_streams"
    __table_args__ = (Index("ix_streams_event_type", "event_pk", "stream_type"),)

    stream_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    stream_type: Mapped[str] = mapped_column(String(32))
    camera_position: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requirement: Mapped[str] = mapped_column(String(16), default="optional")  # required | optional | ignore

    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sample_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    expected_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    availability_status: Mapped[str] = mapped_column(String(32), default="unknown")
    availability_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sync_offset_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_gap_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)

    event: Mapped[Event] = relationship(back_populates="streams")


class EgoPose(Base):
    """A resampled ego pose on the master timeline.

    Coordinates are stored in a local metric frame (east/north metres relative
    to the event origin). Precise global coordinates never leave the approved
    environment and are redacted from exports.
    """

    __tablename__ = "ego_poses"
    __table_args__ = (Index("ix_poses_event_t", "event_pk", "t_rel_s"),)

    pose_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    t_rel_s: Mapped[float] = mapped_column(Float)
    x_m: Mapped[float] = mapped_column(Float)
    y_m: Mapped[float] = mapped_column(Float)
    heading_rad: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    accel_mps2: Mapped[float | None] = mapped_column(Float, nullable=True)
    steering_rad: Mapped[float | None] = mapped_column(Float, nullable=True)
    arc_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    localization_quality: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped[Event] = relationship(back_populates="poses")


class MapFeature(Base):
    __tablename__ = "map_features"
    __table_args__ = (Index("ix_mapfeat_event_type", "event_pk", "feature_type"),)

    feature_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    feature_id: Mapped[str] = mapped_column(String(128), default="")
    feature_type: Mapped[str] = mapped_column(String(48))
    # junction | lane_centerline | lane_boundary | stop_line | traffic_signal | bus_stop
    geometry: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # GeoJSON-shaped, local metric frame
    topology_attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    map_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped[Event] = relationship(back_populates="map_features")


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (Index("ix_det_event_track", "event_pk", "track_id"),)

    detection_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    t_rel_s: Mapped[float] = mapped_column(Float)
    camera: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="perception")  # perception | reference
    object_type: Mapped[str] = mapped_column(String(48))
    object_subtype: Mapped[str | None] = mapped_column(String(48), nullable=True)
    track_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bounding_box: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {x,y,w,h} normalised
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. traffic light state
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    velocity_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    lane_relation: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    event: Mapped[Event] = relationship(back_populates="detections")


class FieldRecommendation(Base):
    """A machine recommendation for a single field, with its own confidence."""

    __tablename__ = "field_recommendations"
    __table_args__ = (
        UniqueConstraint("event_pk", "field_name", name="uq_recommendation_event_field"),
        Index("ix_reco_status", "status"),
    )

    recommendation_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    field_name: Mapped[str] = mapped_column(String(96))
    original_value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommended_value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    alternatives_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_band: Mapped[str] = mapped_column(String(24), default="manual")
    confidence_components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    method: Mapped[str] = mapped_column(String(64), default="analytic")
    explanation: Mapped[str] = mapped_column(Text, default="")
    auto_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(32), default="AUTO_PREPARED")
    model_or_rule_version: Mapped[str] = mapped_column(String(96), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[Event] = relationship(back_populates="recommendations")


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (Index("ix_validation_event_rule", "event_pk", "rule_name"),)

    validation_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    rule_name: Mapped[str] = mapped_column(String(96), index=True)
    category: Mapped[str] = mapped_column(String(48), default="OTHER")
    severity: Mapped[str] = mapped_column(String(16), default="WARNING")
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    observed_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommended_correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocks_export: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_version: Mapped[str] = mapped_column(String(24), default="1.0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[Event] = relationship(back_populates="validations")


class Evidence(Base):
    __tablename__ = "evidence"

    evidence_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    evidence_id: Mapped[str] = mapped_column(String(96), index=True)
    purpose: Mapped[str] = mapped_column(String(64))
    camera: Mapped[str | None] = mapped_column(String(32), nullable=True)
    t_rel_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="json")  # image | json | csv | svg
    relative_path: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    redaction_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    unavailable_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[Event] = relationship(back_populates="evidence")


class Review(Base):
    """A human decision on a single field. Append-only history."""

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_review_event_field", "event_pk", "field_name"),)

    review_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.event_pk", ondelete="CASCADE"), index=True)

    field_name: Mapped[str] = mapped_column(String(96))
    original_recommendation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewer_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(32))  # ACCEPT | REJECT | EDIT
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewer: Mapped[str] = mapped_column(String(128), default="local.tester")
    reviewer_role: Mapped[str] = mapped_column(String(32), default="tester")
    is_senior_review: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)

    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[Event] = relationship(back_populates="reviews")


class AuditEvent(Base):
    """Append-only audit trail. The API exposes no delete or update operation."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_ref"),)

    audit_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    actor: Mapped[str] = mapped_column(String(128), default="system")
    actor_role: Mapped[str] = mapped_column(String(32), default="system")
    action: Mapped[str] = mapped_column(String(96), index=True)
    entity_type: Mapped[str] = mapped_column(String(48), default="")
    entity_ref: Mapped[str] = mapped_column(String(128), default="")
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    software_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ConfigurationProfile(Base):
    """A saved dashboard configuration (filters, sensors, rules, CSV schema).

    Credentials are NEVER stored in a profile - only the id of a connection
    profile whose secrets live in the OS credential store.
    """

    __tablename__ = "configuration_profiles"

    profile_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)

    query_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sensor_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rule_overrides_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    threshold_overrides_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    csv_template_id: Mapped[str] = mapped_column(String(64), default="germany_bus_test")
    csv_columns_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    connection_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    executed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="local.tester")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ConnectionProfile(Base):
    """Non-secret description of a configured data source."""

    __tablename__ = "connection_profiles"

    connection_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(48))
    # data_scout | metadata_api | sensor_store | map_service | database |
    # object_store | labeling_tool | evidence_store | spreadsheet
    adapter: Mapped[str] = mapped_column(String(64))
    integration_type: Mapped[str] = mapped_column(String(32), default="rest_api")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Non-secret settings only. Secret material is referenced by env-var name or
    # credential-store key; the values are never persisted here.
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    field_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    discovered_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Health record (spec section 9). `last_tested_at` is the last *attempt*;
    # `last_success_at` is the last attempt that actually worked. Keeping them
    # apart is the difference between "we tried five minutes ago" and "it has
    # been working for five minutes" - a distinction the Connections page has
    # to be able to draw. No secret value is ever stored here.
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="NOT_CONFIGURED")
    auth_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    permissions_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


ALL_TABLES = [
    AutomationRun,
    Event,
    SensorStream,
    EgoPose,
    MapFeature,
    Detection,
    FieldRecommendation,
    ValidationResult,
    Evidence,
    Review,
    AuditEvent,
    ConfigurationProfile,
    ConnectionProfile,
]
