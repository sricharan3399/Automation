# Validation Rules

Rules live in `config/validation_rules.yaml`. Edit that file and press **Reload from disk**
on the Validation Rules page — no restart, no code change.

## Anatomy of a rule

```yaml
- id: TEMPORAL_ENTRY_BEFORE_EXIT
  category: TEMPORAL
  description: "Junction entry must precede junction exit."
  inputs: [junction_entry_time, junction_exit_time]
  enabled: true
  severity: ERROR          # BLOCKING | ERROR | WARNING | INFO
  blocks_processing: false # stop the per-event pipeline on failure
  blocks_export: true      # the record cannot enter the production CSV
  requires_review: true    # route to the human review queue
  threshold: null
  threshold_source: none   # config | project | none
  version: "1.0"
```

`version` is recorded on every result, so a finding can always be traced to the rule
revision that produced it.

## Three outcomes, never conflated

| Outcome | Meaning |
|---|---|
| **passed** | The rule ran and the condition held |
| **failed** | The rule ran and the condition did not hold |
| **skipped** | The rule could not run, with a stated reason |

A skipped rule is never reported as a pass. This is what stops a QA report from implying
coverage the run did not have.

A rule is skipped when its inputs are unavailable, when it needs reference annotations that
do not exist, when it awaits an approved project threshold, or when it is catalogued but
not implemented in this build.

## Thresholds

`threshold_source` says where a rule's threshold comes from:

* `none` — the rule is a structural check with no threshold.
* `config` — from `config/base.yaml`, a platform data-quality value, tunable by an
  administrator.
* `project` — **an approved project value that has not been supplied.** These rules ship
  **disabled** and appear in the dashboard as `AWAITING APPROVED PROJECT THRESHOLD`. They
  are skipped with that reason.

The platform does not invent a safety-relevant threshold. Reporting a distance error
against a made-up tolerance would be worse than reporting nothing.

Currently awaiting approved values:

* `BUS_BOUNDING_BOX_MISMATCH` — IoU floor against reference
* `BUS_DISTANCE_ESTIMATION_ERROR` — longitudinal distance tolerance
* `BUS_VELOCITY_ESTIMATION_ERROR` — velocity tolerance

Supply the value and set `enabled: true` to activate each.

## Two vocabularies

Rules are grouped by **rule category** on the Validation Rules page (Temporal, Geometry,
Synchronization, …). The CSV records the controlled **abnormality category** vocabulary
(TIMESTAMP, GEOMETRY, SYNCHRONIZATION, …). These are related but not identical — `TEMPORAL`
rules produce `TIMESTAMP` abnormalities — and the mapping lives in
`backend/validation/engine.py`.

---

## The catalogue

### Temporal

| Rule | Severity | Checks |
|---|---|---|
| `TEMPORAL_EVAL_WINDOW_ORDER` | BLOCKING | The evaluation window starts before it ends |
| `TEMPORAL_EVENT_IN_WINDOW` | ERROR | The event time falls inside the window |
| `TEMPORAL_DISTANCE_MARKER_ORDER` | ERROR | 200 m < 100 m < 60 m < entry < exit, along the direction of travel |
| `TEMPORAL_ENTRY_BEFORE_EXIT` | ERROR | Entry precedes exit |
| `TEMPORAL_WAIT_LINE_BEFORE_ENTRY` | WARNING | The wait line is crossed at or before entry |
| `TEMPORAL_FIRST_SEEN_BEFORE_FULL_VIEW` | ERROR | First visible is at or before full view |
| `TEMPORAL_WITHIN_SOURCE_DURATION` | BLOCKING | Every derived timestamp lies inside the recorded clip |

### Geometry and map

| Rule | Severity | Checks |
|---|---|---|
| `GEOMETRY_POLYGON_MIN_POINTS` | BLOCKING | At least 3 unique, non-collinear points |
| `GEOMETRY_POLYGON_VALID` | BLOCKING | A simple, non self-intersecting ring |
| `GEOMETRY_POLYGON_AREA_PLAUSIBLE` | WARNING | Area within the configured range |
| `GEOMETRY_TRAJECTORY_INTERSECTS_JUNCTION` | ERROR | The ego actually passes through it |
| `GEOMETRY_ENTRY_EDGE_ON_POLYGON` | ERROR | The entry edge belongs to the polygon |
| `GEOMETRY_EXIT_EDGE_ON_POLYGON` | ERROR | The exit edge belongs to the polygon |
| `GEOMETRY_EGO_CROSSES_ENTRY_EDGE` | ERROR | The trajectory crosses the nominated entry edge |
| `GEOMETRY_EGO_CROSSES_EXIT_EDGE` | ERROR | The trajectory crosses the nominated exit edge |
| `GEOMETRY_TOPOLOGY_PLAUSIBLE` | WARNING | Entry and exit are distinct |
| `GEOMETRY_MAP_ALIGNMENT` | WARNING | Lateral offset to the mapped centreline is within tolerance |
| `MAP_CONTEXT_AVAILABLE` | ERROR | Map context exists for this event |
| `MAP_VERSION_RECORDED` | WARNING | The map version is recorded for reproducibility |

When a polygon fails, a **recommended** replacement (the convex hull of the supplied
points) is offered. It is never applied automatically.

### Synchronisation

| Rule | Severity | Checks |
|---|---|---|
| `SYNC_TIMESTAMP_MONOTONIC` | ERROR | Timestamps are non-decreasing |
| `SYNC_CAMERA_OFFSET` | WARNING | Camera-to-master offset within budget |
| `SYNC_TELEMETRY_OFFSET` | WARNING | Telemetry-to-master offset within budget |
| `SYNC_TIMESTAMP_GAP` | WARNING | No oversized sample gap |
| `SYNC_DUPLICATE_TIMESTAMPS` | WARNING | No duplicate sample timestamps |

### Sensor and data quality

| Rule | Severity | Checks |
|---|---|---|
| `SENSOR_REQUIRED_STREAM_PRESENT` | BLOCKING | Every Required stream exists — **blocks processing** |
| `SENSOR_STREAM_AVAILABILITY` | ERROR | Required streams meet the availability floor |
| `SENSOR_FROZEN_STREAM` | ERROR | No mandatory stream is frozen |
| `SENSOR_DROPPED_FRAMES` | WARNING | Camera frame drops against the declared rate |
| `SENSOR_DUPLICATE_FRAMES` | WARNING | Repeated identical frames |
| `DATA_MANDATORY_FIELDS` | BLOCKING | All mandatory canonical fields populated |
| `DATA_LOCALIZATION_QUALITY` | WARNING | Localisation good enough for distance-based timestamps |
| `DATA_COUNTRY_AUTHORITATIVE` | ERROR | Country came from authoritative metadata, **not** a filename |

`SENSOR_REQUIRED_STREAM_PRESENT` is the only rule that stops the pipeline. The event is
routed to data review with `BLOCKED_DATA_ERROR`, and the analytical stages are deliberately
skipped — deriving geometry from data already known to be unusable would manufacture
findings nobody should act on.

### Perception (candidates)

| Rule | Severity | Needs reference data |
|---|---|---|
| `BUS_MISSED_DETECTION` | WARNING | yes |
| `BUS_FALSE_POSITIVE` | WARNING | yes |
| `BUS_WRONG_CLASSIFICATION` | WARNING | yes |
| `LOW_CONFIDENCE_BUS` | INFO | no |
| `BUS_BOUNDING_BOX_MISMATCH` | WARNING | yes — awaiting threshold |
| `BUS_DISTANCE_ESTIMATION_ERROR` | WARNING | yes — awaiting threshold |
| `BUS_VELOCITY_ESTIMATION_ERROR` | WARNING | yes — awaiting threshold |

Without reference annotations these are skipped, and the report states that the absence of
findings is **not** evidence that perception was correct.

### Tracking

`BUS_TRACK_ID_SWITCH`, `BUS_TRACK_LOSS`, `BUS_TRACK_FRAGMENTATION`, `BUS_DUPLICATE_TRACK`
(all WARNING) and `BUS_TEMPORARY_LOSS` (INFO).

Identity switching is inferred from spatial continuity: one track ending and a different
track of the same class beginning at nearly the same place and instant is the signature of
an identity change rather than two objects.

### Behaviour — observations, never verdicts

| Rule | Severity | Produces |
|---|---|---|
| `BEHAVIOR_STOP_OBSERVATION` | INFO | The measurement, verbatim. Never fails |
| `BEHAVIOR_ROLLING_STOP_CANDIDATE` | WARNING | A candidate for human interpretation |

The engine writes:

> Ego speed remained at or below the configured stop threshold of 0.30 m/s for 2.10 s.

It never writes:

> Vehicle stopped because of crossing traffic.

The `interpretation` field on every observation is left empty and is only ever filled in by
a human reviewer.

### Traffic control

| Rule | Severity | Checks |
|---|---|---|
| `TRAFFIC_CONTROL_PRESENT` | WARNING | A signalised junction has a control entity in context |
| `TRAFFIC_LIGHT_STATE_CONSISTENCY` | WARNING | Observed transitions are legal for the region |

The signal model is region-configurable. Germany and Austria include the red-yellow phase;
the default model does not.

### Duplicate

`DUPLICATE_CANONICAL_KEY` (INFO) records whether a record was inserted or upserted. The
canonical key is a SHA-256 over the anonymised session reference, the canonical event type,
the rounded event time and the target junction — so re-processing updates the existing
record and preserves reviewer decisions.

### CSV export

`CSV_MANDATORY_VALUES`, `CSV_ENUM_VALIDITY`, `CSV_TIMESTAMP_ORDERING`, `CSV_DUPLICATE_KEYS`,
`CSV_CONFIDENCE_RANGE`, `CSV_COUNTRY_CONSISTENCY`, `CSV_ENCODING_AND_ESCAPING`.

These run over assembled export rows, not single events. Encoding and escaping also guards
against CSV injection: a value starting with `=`, `+`, `-` or `@` is prefixed so a
spreadsheet cannot execute it.

---

## Adding a rule

1. Add the entry to `config/validation_rules.yaml`.
2. Implement it in the matching `backend/validation/rules_*.py`:

```python
@rule("MY_NEW_RULE")
def my_new_rule(ctx: ValidationContext, definition: RuleDefinition) -> ValidationOutcome:
    value = ctx.metadata.lane_count
    if value is None:
        return skip(definition, "The event has no lane_count to check.")
    if value <= 6:
        return ok(definition, f"Lane count {value} is within the expected range.")
    return fail(
        definition,
        f"Lane count {value} exceeds the expected maximum.",
        correction="Confirm the mapped lane count for this road segment.",
        observed={"lane_count": value},
    )
```

3. Add a test asserting both the pass and the fail path.

A rule listed in the catalogue but not implemented is skipped with that reason — it is
never quietly treated as passing.
