/** Shared types mirroring the backend's versioned JSON contracts. */

export type RecordStatus =
  | 'CANDIDATE'
  | 'AUTO_PREPARED'
  | 'REVIEW_REQUIRED'
  | 'CONFIRMED_BY_TESTER'
  | 'REJECTED_BY_TESTER'
  | 'BLOCKED_DATA_ERROR'
  | 'SENIOR_REVIEW_REQUIRED'

export type Severity = 'BLOCKING' | 'ERROR' | 'WARNING' | 'INFO'
export type ConfidenceBand = 'auto_confirm' | 'verify' | 'suggest' | 'manual'
export type StreamRequirement = 'required' | 'optional' | 'ignore'

export interface DateTimeRange {
  start_date?: string | null
  end_date?: string | null
  start_time?: string | null
  end_time?: string | null
  day_only: boolean
  night_only: boolean
  weekdays_only: boolean
  weekends_only: boolean
}

export interface LaneFilter {
  lane_count_any: boolean
  lane_count_exact: number[]
  min_lanes?: number | null
  max_lanes?: number | null
  lane_configuration: string[]
  ego_lane_relation: string[]
}

export interface DatasetSelection {
  project?: string | null
  dataset?: string | null
  dataset_version?: string | null
  drive_collection?: string | null
  vehicle_build?: string | null
  software_version?: string | null
  map_version?: string | null
}

export interface ScoutQuery {
  contract_version?: string
  country_code?: string | null
  country?: string | null
  regions: string[]
  cities: string[]
  test_areas: string[]
  routes: string[]
  object_types: string[]
  bus_subtypes: string[]
  scenario_tags: string[]
  road_types: string[]
  lanes: LaneFilter
  intersection_types: string[]
  intersection_complexity: string[]
  traffic_control_entities: string[]
  traffic_light_states: string[]
  vehicle_maneuvers: string[]
  weather: string[]
  lighting: string[]
  time_range: DateTimeRange
  dataset: DatasetSelection
  error_detection: string[]
  limit?: number | null
}

export interface StreamRequirementSpec {
  stream_type: string
  camera_position?: string | null
  requirement: StreamRequirement
}

export interface SensorConfiguration {
  contract_version?: string
  streams: StreamRequirementSpec[]
}

export interface FilterField {
  values: string[]
  origin: 'source' | 'fallback'
}

export interface FilterVocabulary {
  fields: Record<string, FilterField>
  source_available: boolean
  note: string
  connection_id?: string
  adapter?: string
  source_error?: string
  countries?: { default?: string; allowed?: { code: string; name: string }[] }
}

export interface ConnectionSummary {
  connection_id: string
  display_name: string
  kind: string
  adapter: string
  integration_type: string
  enabled: boolean
  configured: boolean
  last_status: string
  last_tested_at?: string | null
  last_latency_ms?: number | null
  last_error?: string | null
  api_version?: string | null
  schema_version?: string | null
  permissions: string[]
  settings: Record<string, unknown>
  credential_available?: boolean | null
  has_field_mapping: boolean
  read_only: boolean
}

export interface RunCounters {
  records_discovered: number
  records_scanned: number
  records_processed: number
  records_matched_country: number
  records_matched_scenario: number
  candidate_issue_count: number
  blocking_error_count: number
  review_required_count: number
  duplicates_merged: number
  csv_rows_created: number
  error_count: number
}

export interface RunSummary {
  run_id: string
  status: string
  stage: string
  stage_order: string[]
  completed_stages: string[]
  dry_run: boolean
  active: boolean
  profile_id?: string | null
  connection_profile_id?: string | null
  adapter?: string | null
  query: Record<string, unknown>
  counters: RunCounters
  versions: Record<string, string | null>
  checkpoint: Record<string, unknown>
  message?: string | null
  output_dir?: string | null
  elapsed_seconds?: number | null
  started_at?: string | null
  finished_at?: string | null
  created_at: string
  created_by: string
}

export interface QueryPreview {
  summary: Record<string, string>
  native_query: Record<string, unknown>
  estimated_records?: number | null
  estimate_is_exact: boolean
  estimate_note: string
  warnings: string[]
  adapter: string
}

export interface RuleDefinition {
  id: string
  category: string
  description: string
  enabled: boolean
  severity: Severity
  blocks_processing: boolean
  blocks_export: boolean
  requires_review: boolean
  threshold?: number | null
  threshold_source: string
  version: string
  inputs: string[]
  requires_reference_data: boolean
  awaiting_project_threshold: boolean
  implemented: boolean
  state: string
}

export interface EventSummary {
  canonical_event_key: string
  event_reference: string
  session_reference: string
  country?: string | null
  country_code?: string | null
  region?: string | null
  city?: string | null
  event_type: string
  event_time?: string | null
  object_types: string[]
  bus_type?: string | null
  scenario_tags: string[]
  road_type?: string | null
  lane_count?: number | null
  lane_relation?: string | null
  intersection_type?: string | null
  intersection_complexity?: string | null
  weather?: string | null
  lighting?: string | null
  status: RecordStatus
  record_version: number
  overall_confidence?: number | null
  blocking_error_count: number
  review_required: boolean
  abnormality_categories: string[]
  synchronization_quality?: string | null
  is_synthetic: boolean
  updated_at: string
}

export interface ConfidenceExplanation {
  components: Record<string, number>
  weights: Record<string, number>
  missing_components: string[]
  final: number
  narrative: string
}

export interface ReviewField {
  field: string
  original: unknown
  recommended: unknown
  alternatives: Record<string, unknown>[]
  confidence: number
  band: ConfidenceBand
  explanation: ConfidenceExplanation
  reason: string
  auto_selected: boolean
  safety_critical: boolean
  status: RecordStatus
  validation: { rule_id: string; severity: Severity; message: string }[]
  reviewer_value: unknown
  reviewer_decision?: string | null
  reviewer?: string | null
  reviewed_at?: string | null
}

export interface ReviewDetail {
  canonical_event_key: string
  event_reference: string
  status: RecordStatus
  overall_confidence?: number | null
  blocking_error_count: number
  automation_recommendation?: string | null
  fields: ReviewField[]
  failures: {
    rule_id: string
    category: string
    severity: Severity
    message: string
    recommended_correction?: string | null
    blocks_export: boolean
  }[]
}

export interface ExportReadiness {
  passed: number
  warnings: number
  blocking_errors: number
  ready: boolean
  total_rows: number
  exportable_rows: number
  rejected_rows: number
  issues: Record<string, unknown>[]
}

export interface MapFeature {
  feature_id: string
  feature_type: string
  geometry: { type: string; coordinates: unknown }
  attributes: Record<string, unknown>
  map_version?: string | null
  confidence?: number | null
}

export interface TrajectoryPoint {
  t: number
  x_m: number
  y_m: number
  heading_rad?: number | null
  speed_mps?: number | null
  arc_length_m?: number | null
}

export interface ProgressFrame {
  run_id: string
  status: string
  stage: string
  completed_stages?: string[]
  stage_order?: string[]
  records_discovered?: number
  records_scanned?: number
  records_processed?: number
  candidate_issue_count?: number
  blocking_error_count?: number
  review_required_count?: number
  filtered_out?: number
  error_count?: number
  elapsed_s?: number
  estimated_remaining_s?: number | null
  current_event_ref?: string | null
  current_status?: string | null
  message?: string | null
  outputs?: Record<string, unknown>
}
