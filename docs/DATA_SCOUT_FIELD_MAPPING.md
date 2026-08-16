# Data Scout field mapping

Every value the platform stores carries a provenance record. That is only
meaningful if the mapping from source field to canonical field is recorded
deliberately rather than assumed, so this table is the authoritative record of
what came from where.

**Every Data Scout column below is `TBD`.** No field name has been guessed. The
adapter will not run until the required rows are filled in and saved, because a
wrong mapping produces confidently wrong AV findings — the most expensive kind
of defect this platform can create.

Fill this in from answers 10–17 of
[DATA_SCOUT_REAL_CONNECTION.md](DATA_SCOUT_REAL_CONNECTION.md), then enter the
same mapping in the dashboard under **Connections → Data Scout → Field
Mapping**, which is what the adapter actually reads. This file is the reviewed
record; the stored mapping is the executable one.

---

## Required

The pipeline cannot identify or deduplicate an event without these.

| Canonical field | Data Scout field | Type | Required | Notes |
|---|---|---|---|---|
| `event_id` | TBD | string | **YES** | Must be stable across queries. Deduplication depends on it. |
| `session_id` | TBD | string | | Groups events from one drive. |
| `country_code` | TBD | string | **YES** | ISO 3166-1 alpha-2. `DE` for the Germany Bus profile. |
| `event_time` | TBD | timestamp | **YES** | Record the exact format and timezone in the notes column. |

## Scene classification

Drive filtering and scenario analysis. A missing value is stored as `UNKNOWN`,
never inferred.

| Canonical field | Data Scout field | Type | Required | Notes |
|---|---|---|---|---|
| `object_type` | TBD | string | | `bus` for this profile. |
| `object_subtype` | TBD | string | | e.g. articulated, school, coach. |
| `scenario_type` | TBD | string | | Source taxonomy; mapped to the platform's vocabulary. |
| `road_type` | TBD | string | | urban / rural / autobahn / motorway / ramp / residential. |
| `lane_count` | TBD | integer | | If absent, derived from map topology, else `UNKNOWN`. |
| `lane_relation` | TBD | string | | ego lane vs object lane. |
| `intersection_type` | TBD | string | | |
| `traffic_control_entity` | TBD | string | | traffic light, sign, none. |
| `region` | TBD | string | | |
| `city` | TBD | string | | |
| `weather` | TBD | string | | |
| `lighting` | TBD | string | | |

## Provenance and versioning

Required for traceability of an exported CSV back to the exact source state.

| Canonical field | Data Scout field | Type | Required | Notes |
|---|---|---|---|---|
| `dataset` | TBD | string | | |
| `dataset_version` | TBD | string | | |
| `map_version` | TBD | string | | Needed to interpret map alignment results. |
| `vehicle_software_version` | TBD | string | | The AV stack build under test. |
| `source_record_version` | TBD | string | | Detects a record changing underneath a run. |

## Per-event retrieval

Not columns but endpoints — each maps to one adapter operation. An unsupported
retrieval is reported as `NOT SUPPORTED BY SOURCE`; it never becomes a
fabricated empty result.

| Canonical operation | Data Scout endpoint | Supported? | Consequence if absent |
|---|---|---|---|
| `get_sensor_manifest` | TBD | TBD | Streams report `MISSING`; sensor rules cannot evaluate. |
| `get_trajectory` | TBD | TBD | Geometry, timestamps and distance markers are skipped. |
| `get_map_context` | TBD | TBD | Map alignment and junction ranking are skipped. |
| `get_annotations` | TBD | TBD | **No ground truth: perception comparison is disabled entirely.** |
| `get_perception_results` | TBD | TBD | Detection-vs-reference findings unavailable. |

That fourth row is the important one. With no reference annotations the
platform will not emit `BUS_MISSED_DETECTION`, `BUS_FALSE_POSITIVE` or any
other comparison finding — it marks the analysis unavailable, because claiming
a missed detection without ground truth is an unfounded safety-critical
assertion.

---

## Operations the adapter interface names differently

Recorded here rather than renamed in code. The working interface is tested and
in use; churning it for cosmetic alignment would risk the AV logic for no
functional gain.

| Brief (section 11) | Implemented as | Notes |
|---|---|---|
| `count_events` | `estimate_count` | Returns `(count, is_exact, source)` so an estimate is never mistaken for an exact count. |
| `get_filter_options` | `get_supported_filters` | |
| `get_event` | `get_event_bundle` | Returns the full bundle rather than a bare record. |
| `get_capabilities` | *(not implemented)* | Capabilities are currently inferred from which endpoints are configured. |
| `get_prediction_results` | *(not implemented)* | No approved source defined. |
| `get_planning_results` | *(not implemented)* | No approved source defined. |

---

## Schema change detection

Once a mapping is saved, the adapter records the schema version alongside it.
On each connection the current source schema is compared against the stored
one, and new, removed, retyped or re-enumerated fields raise:

```
SOURCE SCHEMA CHANGED
3 fields changed.
Review field mapping before starting production run.
```

If a **required** field disappears, processing of affected records stops. It
does not continue with a default value substituted — that would silently
convert a source outage into plausible-looking output.
