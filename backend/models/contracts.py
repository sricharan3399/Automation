"""Versioned JSON contracts exchanged between pipeline modules.

Every module in the pipeline consumes and produces these Pydantic models, and
every top-level payload carries ``contract_version``. This is what makes the
pipeline stages independently testable and lets a stored run be replayed
against a later software version with a clear compatibility signal.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.version import CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Enumerations that the platform's own logic depends on.
# (Source vocabularies stay dynamic - see backend/connectors/capabilities.py.)
# ---------------------------------------------------------------------------
class RecordStatus(str, Enum):
    """Machine findings never jump straight to a confirmed defect."""

    CANDIDATE = "CANDIDATE"
    AUTO_PREPARED = "AUTO_PREPARED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CONFIRMED_BY_TESTER = "CONFIRMED_BY_TESTER"
    REJECTED_BY_TESTER = "REJECTED_BY_TESTER"
    BLOCKED_DATA_ERROR = "BLOCKED_DATA_ERROR"
    SENIOR_REVIEW_REQUIRED = "SENIOR_REVIEW_REQUIRED"


class Severity(str, Enum):
    BLOCKING = "BLOCKING"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class AbnormalityCategory(str, Enum):
    PERCEPTION = "PERCEPTION"
    TRACKING = "TRACKING"
    SENSOR = "SENSOR"
    SYNCHRONIZATION = "SYNCHRONIZATION"
    LOCALIZATION = "LOCALIZATION"
    MAP = "MAP"
    GEOMETRY = "GEOMETRY"
    TIMESTAMP = "TIMESTAMP"
    PLANNING = "PLANNING"
    PREDICTION = "PREDICTION"
    TRAFFIC_CONTROL = "TRAFFIC_CONTROL"
    DATA_QUALITY = "DATA_QUALITY"
    ANNOTATION = "ANNOTATION"
    METADATA = "METADATA"
    DUPLICATE = "DUPLICATE"
    BEHAVIOR = "BEHAVIOR"
    CSV = "CSV"
    OTHER = "OTHER"


class StreamRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    IGNORE = "ignore"


class ConfidenceBand(str, Enum):
    AUTO_CONFIRM = "auto_confirm"
    VERIFY = "verify"
    SUGGEST = "suggest"
    MANUAL = "manual"


class Contract(BaseModel):
    """Base for every top-level contract payload."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    contract_version: str = CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Dashboard query (Scout Setup page -> adapter)
# ---------------------------------------------------------------------------
class DateTimeRange(BaseModel):
    start_date: str | None = None  # ISO date, YYYY-MM-DD
    end_date: str | None = None
    start_time: str | None = None  # HH:MM
    end_time: str | None = None
    day_only: bool = False
    night_only: bool = False
    weekdays_only: bool = False
    weekends_only: bool = False


class LaneFilter(BaseModel):
    lane_count_any: bool = True
    lane_count_exact: list[int] = Field(default_factory=list)
    min_lanes: int | None = None
    max_lanes: int | None = None
    lane_configuration: list[str] = Field(default_factory=list)
    ego_lane_relation: list[str] = Field(default_factory=list)


class DatasetSelection(BaseModel):
    project: str | None = None
    dataset: str | None = None
    dataset_version: str | None = None
    drive_collection: str | None = None
    vehicle_build: str | None = None
    software_version: str | None = None
    map_version: str | None = None


class ScoutQuery(Contract):
    """The complete visual query built on the Scout Setup page.

    Empty list == "Any" for every multi-select field.
    """

    country_code: str | None = None
    country: str | None = None
    regions: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    test_areas: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)

    object_types: list[str] = Field(default_factory=list)
    bus_subtypes: list[str] = Field(default_factory=list)
    scenario_tags: list[str] = Field(default_factory=list)

    road_types: list[str] = Field(default_factory=list)
    lanes: LaneFilter = Field(default_factory=LaneFilter)

    intersection_types: list[str] = Field(default_factory=list)
    intersection_complexity: list[str] = Field(default_factory=list)
    traffic_control_entities: list[str] = Field(default_factory=list)
    traffic_light_states: list[str] = Field(default_factory=list)
    vehicle_maneuvers: list[str] = Field(default_factory=list)

    weather: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)

    time_range: DateTimeRange = Field(default_factory=DateTimeRange)
    dataset: DatasetSelection = Field(default_factory=DatasetSelection)

    error_detection: list[str] = Field(default_factory=list)
    limit: int | None = None

    @field_validator("country_code")
    @classmethod
    def _upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    def is_empty(self) -> bool:
        """True when the query would match everything the source can return."""
        return not any(
            [
                self.country_code,
                self.regions,
                self.cities,
                self.test_areas,
                self.routes,
                self.object_types,
                self.bus_subtypes,
                self.scenario_tags,
                self.road_types,
                self.intersection_types,
                self.traffic_control_entities,
                self.weather,
                self.lighting,
                self.time_range.start_date,
                self.time_range.end_date,
            ]
        )


class StreamRequirementSpec(BaseModel):
    stream_type: str
    camera_position: str | None = None
    requirement: StreamRequirement = StreamRequirement.OPTIONAL

    @property
    def key(self) -> str:
        return f"{self.stream_type}:{self.camera_position}" if self.camera_position else self.stream_type


class SensorConfiguration(Contract):
    """Which streams the tester requires for this run."""

    streams: list[StreamRequirementSpec] = Field(default_factory=list)

    def required_keys(self) -> list[str]:
        return [s.key for s in self.streams if s.requirement == StreamRequirement.REQUIRED]

    def requirement_for(self, stream_type: str, camera_position: str | None = None) -> StreamRequirement:
        exact = f"{stream_type}:{camera_position}" if camera_position else stream_type
        for spec in self.streams:
            if spec.key == exact:
                return spec.requirement
        for spec in self.streams:
            if spec.stream_type == stream_type and spec.camera_position is None:
                return spec.requirement
        return StreamRequirement.OPTIONAL

    @classmethod
    def default(cls) -> SensorConfiguration:
        """Source-required defaults: nothing is forced beyond the master clock.

        The tester raises requirements on the Sensor Configuration page. The
        platform does not silently demand streams the project has not agreed to.
        """
        return cls(
            streams=[
                StreamRequirementSpec(stream_type="vehicle_state", requirement=StreamRequirement.REQUIRED),
                StreamRequirementSpec(stream_type="localization", requirement=StreamRequirement.OPTIONAL),
                StreamRequirementSpec(
                    stream_type="camera", camera_position="front_main", requirement=StreamRequirement.OPTIONAL
                ),
            ]
        )


# ---------------------------------------------------------------------------
# Adapter output: the normalised event bundle
# ---------------------------------------------------------------------------
class EventMetadata(Contract):
    """Canonical, source-independent event metadata."""

    event_id: str
    session_id: str = ""
    job_ref: str | None = None

    country: str | None = None
    country_code: str | None = None
    # Which source field the country was resolved from. Filename-derived values
    # are recorded as such and rejected by DATA_COUNTRY_AUTHORITATIVE.
    country_source_field: str | None = None
    region: str | None = None
    city: str | None = None
    test_area: str | None = None
    route: str | None = None

    event_time: datetime | None = None
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    duration_s: float | None = None

    road_type: str | None = None
    lane_count: int | None = None
    lane_id: str | None = None
    lane_configuration: list[str] = Field(default_factory=list)
    intersection_type: str | None = None
    intersection_complexity: str | None = None
    traffic_control_entity: list[str] = Field(default_factory=list)
    traffic_light_state: str | None = None
    weather: str | None = None
    lighting: str | None = None

    object_type: list[str] = Field(default_factory=list)
    bus_type: str | None = None
    scenario_tags: list[str] = Field(default_factory=list)
    vehicle_maneuver: str | None = None

    project: str | None = None
    dataset: str | None = None
    dataset_version: str | None = None
    drive_collection: str | None = None
    vehicle_build: str | None = None
    software_version: str | None = None
    map_version: str | None = None

    event_type: str = "unknown"
    # Fields the source provided that no canonical field claimed. Surfaced in
    # the UI as "unmapped" rather than discarded.
    unmapped: dict[str, Any] = Field(default_factory=dict)


class StreamSample(BaseModel):
    """One sample on a stream's own clock."""

    t: float  # seconds relative to the event's source origin
    # A cheap content signature used for frozen/duplicate detection. Adapters
    # supply a hash of the payload; no raw sensor data is carried in-contract.
    signature: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StreamManifestEntry(Contract):
    stream_type: str
    camera_position: str | None = None
    present: bool = True
    start_t: float | None = None
    end_t: float | None = None
    nominal_rate_hz: float | None = None
    sample_count: int = 0
    samples: list[StreamSample] = Field(default_factory=list)
    declared_offset_ms: float | None = None
    uri: str | None = None  # opaque reference resolved only inside the environment
    notes: str | None = None


class PoseSample(BaseModel):
    t: float
    x_m: float
    y_m: float
    heading_rad: float | None = None
    speed_mps: float | None = None
    accel_mps2: float | None = None
    steering_rad: float | None = None
    localization_quality: float | None = None  # 0..1


class MapGeometry(BaseModel):
    """GeoJSON-shaped geometry in the event's local metric frame (metres)."""

    type: Literal["Point", "LineString", "Polygon"]
    coordinates: list[Any]


class MapFeatureContract(BaseModel):
    feature_id: str
    feature_type: str  # junction | lane_centerline | lane_boundary | stop_line | traffic_signal | bus_stop
    geometry: MapGeometry
    attributes: dict[str, Any] = Field(default_factory=dict)
    map_version: str | None = None
    confidence: float | None = None


class MapContext(Contract):
    available: bool = True
    map_version: str | None = None
    origin_note: str = "local metric frame, metres, x=east y=north"
    features: list[MapFeatureContract] = Field(default_factory=list)
    unavailable_reason: str | None = None

    def by_type(self, feature_type: str) -> list[MapFeatureContract]:
        return [f for f in self.features if f.feature_type == feature_type]


class DetectionContract(BaseModel):
    t: float
    camera: str | None = None
    source: Literal["perception", "reference"] = "perception"
    object_type: str
    object_subtype: str | None = None
    track_id: str | None = None
    bounding_box: dict[str, float] = Field(default_factory=dict)  # x,y,w,h normalised 0..1
    state: str | None = None
    distance_m: float | None = None
    velocity_mps: float | None = None
    lane_relation: str | None = None
    confidence: float | None = None
    model_version: str | None = None


class EventBundle(Contract):
    """Everything an adapter can supply for one event.

    Adapters populate what their source actually provides. Missing sections are
    reported as unavailable - they are never synthesised.
    """

    metadata: EventMetadata
    streams: list[StreamManifestEntry] = Field(default_factory=list)
    poses: list[PoseSample] = Field(default_factory=list)
    detections: list[DetectionContract] = Field(default_factory=list)
    map_context: MapContext = Field(default_factory=lambda: MapContext(available=False))
    annotations_available: bool = False
    reference_data_available: bool = False
    source_start_t: float = 0.0
    source_end_t: float | None = None
    adapter: str = "unknown"
    is_synthetic: bool = False


# ---------------------------------------------------------------------------
# Engine outputs
# ---------------------------------------------------------------------------
class StreamHealth(BaseModel):
    stream_type: str
    camera_position: str | None = None
    requirement: StreamRequirement = StreamRequirement.OPTIONAL
    present: bool = False
    availability_pct: float | None = None
    sample_count: int = 0
    expected_sample_count: int | None = None
    sync_offset_ms: float | None = None
    max_gap_ms: float | None = None
    quality_score: float | None = None
    status: str = "unknown"  # ok | degraded | missing | frozen | blocking
    issues: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.stream_type}:{self.camera_position}" if self.camera_position else self.stream_type


class SynchronizationReport(Contract):
    master_stream: str | None = None
    master_start_t: float = 0.0
    master_end_t: float = 0.0
    stream_health: list[StreamHealth] = Field(default_factory=list)
    max_camera_offset_ms: float | None = None
    max_telemetry_offset_ms: float | None = None
    max_gap_ms: float | None = None
    drift_ms_per_s: dict[str, float] = Field(default_factory=dict)
    quality: str = "unknown"  # good | acceptable | degraded | unusable
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)
    has_blocking_errors: bool = False


class TrajectoryPoint(BaseModel):
    t: float
    x_m: float
    y_m: float
    heading_rad: float | None = None
    speed_mps: float | None = None
    arc_length_m: float = 0.0


class Trajectory(Contract):
    points: list[TrajectoryPoint] = Field(default_factory=list)
    total_length_m: float = 0.0
    duration_s: float = 0.0
    localization_quality: float | None = None
    valid: bool = False
    invalid_reason: str | None = None


class JunctionCandidate(BaseModel):
    feature_id: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    map_alignment_confidence: float = 0.0
    distance_to_trajectory_m: float | None = None
    trajectory_intersects: bool = False
    heading_agreement: float | None = None
    time_agreement_s: float | None = None
    road_type_match: bool | None = None
    traffic_control_match: bool | None = None
    camera_visibility: float | None = None
    polygon: list[list[float]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EdgeCandidate(BaseModel):
    edge_id: str
    p1: list[float]
    p2: list[float]
    confidence: float
    crossing_t: float | None = None
    reasons: list[str] = Field(default_factory=list)


class PolygonAssessment(BaseModel):
    point_count: int = 0
    unique_point_count: int = 0
    area_m2: float | None = None
    is_valid: bool = False
    is_simple: bool = False
    collinear: bool = False
    self_intersecting: bool = False
    trajectory_crosses: bool = False
    issues: list[str] = Field(default_factory=list)
    recommended_polygon: list[list[float]] = Field(default_factory=list)
    existing_polygon: list[list[float]] = Field(default_factory=list)
    confidence: float = 0.0


class TimestampMarker(BaseModel):
    name: str
    t: float | None = None
    absolute: datetime | None = None
    distance_m: float | None = None
    method: str = "arc_length_interpolation"
    interpolation_error_s: float | None = None
    pose_quality: float | None = None
    map_confidence: float | None = None
    confidence: float = 0.0
    available: bool = False
    unavailable_reason: str | None = None


class GeometryResult(Contract):
    target_junction: JunctionCandidate | None = None
    alternatives: list[JunctionCandidate] = Field(default_factory=list)
    polygon: PolygonAssessment = Field(default_factory=PolygonAssessment)
    entry_edge: EdgeCandidate | None = None
    exit_edge: EdgeCandidate | None = None
    entry_alternatives: list[EdgeCandidate] = Field(default_factory=list)
    exit_alternatives: list[EdgeCandidate] = Field(default_factory=list)
    markers: list[TimestampMarker] = Field(default_factory=list)
    map_alignment_offset_m: float | None = None
    map_alignment_confidence: float = 0.0
    available: bool = False
    unavailable_reason: str | None = None

    def marker(self, name: str) -> TimestampMarker | None:
        for m in self.markers:
            if m.name == name:
                return m
        return None


class TrackSummary(BaseModel):
    track_id: str
    object_type: str
    first_t: float
    last_t: float
    sample_count: int
    cameras: list[str] = Field(default_factory=list)
    mean_confidence: float | None = None
    max_gap_s: float | None = None
    lane_relations: list[str] = Field(default_factory=list)


class SceneFinding(BaseModel):
    """A machine observation. Always a CANDIDATE until a human confirms it."""

    code: str
    category: AbnormalityCategory
    severity: Severity = Severity.WARNING
    message: str
    t: float | None = None
    track_id: str | None = None
    camera: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: RecordStatus = RecordStatus.CANDIDATE
    requires_review: bool = True


class SceneAnalysis(Contract):
    available: bool = False
    unavailable_reason: str | None = None
    reference_data_available: bool = False
    detection_count: int = 0
    tracks: list[TrackSummary] = Field(default_factory=list)
    target_object_type: str = "bus"
    first_visible_t: float | None = None
    full_view_t: float | None = None
    perception_findings: list[SceneFinding] = Field(default_factory=list)
    tracking_findings: list[SceneFinding] = Field(default_factory=list)
    traffic_light_observations: list[dict[str, Any]] = Field(default_factory=list)
    detected_scenario_tags: list[str] = Field(default_factory=list)
    mean_detection_confidence: float | None = None
    model_versions: list[str] = Field(default_factory=list)


class BehaviorObservation(BaseModel):
    """A measured fact.

    ``observation`` states what was measured. ``interpretation`` is only ever
    populated by a human reviewer - the engine leaves it None.
    """

    name: str
    observation: str
    t_start: float | None = None
    t_end: float | None = None
    value: float | None = None
    unit: str | None = None
    interpretation: str | None = None


class BehaviorAnalysis(Contract):
    available: bool = False
    unavailable_reason: str | None = None
    observations: list[BehaviorObservation] = Field(default_factory=list)
    maneuver: str | None = None
    heading_change_deg: float | None = None
    approach_detected: bool = False
    deceleration_detected: bool = False
    reacceleration_detected: bool = False
    stop_classification: str = "unknown"
    minimum_speed_mps: float | None = None
    stop_duration_s: float | None = None
    wait_line_distance_m: float | None = None
    findings: list[SceneFinding] = Field(default_factory=list)
    confidence: float = 0.0


class ValidationOutcome(BaseModel):
    rule_id: str
    category: str
    severity: Severity
    passed: bool
    skipped: bool = False
    skip_reason: str | None = None
    message: str = ""
    observed: dict[str, Any] = Field(default_factory=dict)
    recommended_correction: str | None = None
    blocks_processing: bool = False
    blocks_export: bool = False
    requires_review: bool = False
    rule_version: str = "1.0"


class ValidationReport(Contract):
    outcomes: list[ValidationOutcome] = Field(default_factory=list)
    rule_version: str = ""

    @property
    def failures(self) -> list[ValidationOutcome]:
        return [o for o in self.outcomes if not o.passed and not o.skipped]

    @property
    def blocking(self) -> list[ValidationOutcome]:
        return [o for o in self.failures if o.severity == Severity.BLOCKING]

    @property
    def export_blocking(self) -> list[ValidationOutcome]:
        return [o for o in self.failures if o.blocks_export]

    @property
    def has_processing_blocker(self) -> bool:
        return any(o.blocks_processing for o in self.failures)

    def counts(self) -> dict[str, int]:
        out = {"passed": 0, "skipped": 0, "BLOCKING": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
        for o in self.outcomes:
            if o.skipped:
                out["skipped"] += 1
            elif o.passed:
                out["passed"] += 1
            else:
                out[o.severity.value] += 1
        return out


class ConfidenceExplanation(BaseModel):
    components: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    missing_components: list[str] = Field(default_factory=list)
    final: float = 0.0
    narrative: str = ""


class FieldRecommendationContract(BaseModel):
    field_name: str
    original_value: Any = None
    recommended_value: Any = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    band: ConfidenceBand = ConfidenceBand.MANUAL
    explanation: ConfidenceExplanation = Field(default_factory=ConfidenceExplanation)
    reason: str = ""
    method: str = "analytic"
    auto_selected: bool = False
    safety_critical: bool = False
    status: RecordStatus = RecordStatus.AUTO_PREPARED
    model_or_rule_version: str = ""


class EvidenceItem(BaseModel):
    evidence_id: str
    purpose: str
    kind: Literal["image", "json", "csv", "svg"] = "json"
    camera: str | None = None
    t_rel_s: float | None = None
    relative_path: str = ""
    content_hash: str | None = None
    redacted: bool = False
    approved: bool = False
    available: bool = True
    unavailable_reason: str | None = None
    redaction_report: dict[str, Any] = Field(default_factory=dict)


class ReviewPackage(Contract):
    """The complete per-event result handed to the Human Review Queue."""

    canonical_event_key: str
    anonymized_event_ref: str
    anonymized_session_ref: str
    anonymized_job_ref: str | None = None

    metadata: EventMetadata
    synchronization: SynchronizationReport = Field(default_factory=SynchronizationReport)
    trajectory_summary: dict[str, Any] = Field(default_factory=dict)
    geometry: GeometryResult = Field(default_factory=GeometryResult)
    scene: SceneAnalysis = Field(default_factory=SceneAnalysis)
    behavior: BehaviorAnalysis = Field(default_factory=BehaviorAnalysis)
    validation: ValidationReport = Field(default_factory=ValidationReport)
    recommendations: list[FieldRecommendationContract] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)

    abnormality_categories: list[str] = Field(default_factory=list)
    overall_confidence: float = 0.0
    automation_recommendation: str = ""
    status: RecordStatus = RecordStatus.CANDIDATE
    blocking_error_count: int = 0
    review_required: bool = True

    software_version: str = ""
    rule_version: str = ""
    model_version: str = ""
    map_version: str | None = None
    record_version: int = 1
    is_synthetic: bool = False


# ---------------------------------------------------------------------------
# Run-level contracts
# ---------------------------------------------------------------------------
class PipelineStage(str, Enum):
    CONNECTION = "connection"
    METADATA = "metadata"
    COUNTRY_FILTER = "country_filter"
    SCENARIO_FILTER = "scenario_filter"
    SENSOR_CHECK = "sensor_check"
    SYNCHRONIZATION = "synchronization"
    MAP_CONTEXT = "map_context"
    SCENE_ANALYSIS = "scene_analysis"
    BEHAVIOR_ANALYSIS = "behavior_analysis"
    VALIDATION = "validation"
    EVIDENCE = "evidence"
    CSV = "csv"


PIPELINE_STAGE_ORDER: list[str] = [s.value for s in PipelineStage]


class RunRequest(Contract):
    profile_id: str | None = None
    query: ScoutQuery = Field(default_factory=ScoutQuery)
    sensor_config: SensorConfiguration = Field(default_factory=SensorConfiguration.default)
    connection_id: str | None = None
    csv_template_id: str = "germany_bus_test"
    dry_run: bool = True
    limit: int | None = None
    rule_overrides: dict[str, bool] = Field(default_factory=dict)
    error_detection: list[str] = Field(default_factory=list)


class QueryPreview(Contract):
    summary: dict[str, Any] = Field(default_factory=dict)
    native_query: dict[str, Any] = Field(default_factory=dict)
    estimated_records: int | None = None
    estimate_is_exact: bool = False
    estimate_note: str = ""
    warnings: list[str] = Field(default_factory=list)
    adapter: str = ""


class RunProgress(Contract):
    run_id: str
    status: str
    stage: str
    completed_stages: list[str] = Field(default_factory=list)
    records_discovered: int = 0
    records_processed: int = 0
    candidate_issue_count: int = 0
    blocking_error_count: int = 0
    review_required_count: int = 0
    elapsed_s: float = 0.0
    estimated_remaining_s: float | None = None
    message: str | None = None
    current_event_ref: str | None = None


class ExportReadiness(Contract):
    passed: int = 0
    warnings: int = 0
    blocking_errors: int = 0
    ready: bool = False
    total_rows: int = 0
    exportable_rows: int = 0
    rejected_rows: int = 0
    issues: list[dict[str, Any]] = Field(default_factory=list)
