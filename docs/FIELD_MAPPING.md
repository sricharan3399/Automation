# Field Mapping

Every source names its fields differently. The platform normalises them onto one canonical
vocabulary so the pipeline, rules and exports work the same whatever the source.

## Discovery

**Connections → SCHEMA → DISCOVER SCHEMA** inspects the fields the source actually returns
and proposes a mapping. Each field comes back with a confidence and the method used:

| Method | Confidence | Meaning |
|---|---|---|
| `exact` | 1.00 | The source field name matches a canonical name or a known alias exactly |
| `normalised` | 0.90 | It matches once case and separators are ignored (`roadClass` → `road_class`) |
| `manual` | 1.00 | An administrator confirmed it |
| `ambiguous` | 0.00 | Another field already claimed that canonical field — resolve it explicitly |
| `unmapped` | 0.00 | No candidate found |

Correct anything wrong in the mapping editor and **SAVE MAPPING**. A confirmed mapping
always beats the auto-suggested one, and the change is written to the audit trail.

Each canonical field may be mapped from only one source field; the API rejects duplicates
with the conflicting names.

If a required canonical field has no source match, discovery says so explicitly rather than
proceeding with a gap.

## Canonical fields

Defined in `config/field_mapping.yaml`.

### Identity

| Canonical | Required | Common aliases |
|---|---|---|
| `event_id` | yes | `event`, `event_uuid`, `id`, `clip_id`, `snippet_id` |
| `session_id` | yes | `session`, `drive_id`, `log_id`, `recording_id` |
| `job_ref` | no | `job`, `job_id`, `task_id`, `batch_id` |

### Geography

| Canonical | Required | Common aliases |
|---|---|---|
| `country_code` | yes | `countryCode`, `iso_country`, `iso3166_1`, `cc` |
| `country` | yes | `country_name`, `nation` |
| `region` | no | `state`, `province`, `bundesland`, `admin_area` |
| `city` | no | `town`, `municipality`, `locality` |
| `test_area` | no | `area`, `site`, `zone` |
| `route` | no | `route_name`, `route_id` |

### Timing

`event_time` (required), `evaluation_start`, `evaluation_end`, `duration_s`.

### Scene

`road_type`, `lane_count`, `lane_id`, `lane_configuration`, `intersection_type`,
`intersection_complexity`, `traffic_control_entity`, `traffic_light_state`, `weather`,
`lighting`.

### Objects

`object_type`, `bus_type`, `scenario_tags`, `vehicle_maneuver`.

### Provenance

`project`, `dataset`, `dataset_version`, `drive_collection`, `vehicle_build`,
`software_version`, `map_version`.

---

## Country resolution

Country is treated more strictly than any other field, because a mis-attributed country
silently corrupts a whole run.

1. `country_code` from an authoritative metadata field wins.
2. If only a country name is present, it resolves to a code through the configured country
   list.
3. **The field the value came from is recorded.** A value derived from a filename or path
   is kept, but marked non-authoritative.

`DATA_COUNTRY_AUTHORITATIVE` then fails any event whose country came from a filename-like
field — `file`, `filename`, `path`, `uri`, `url`, `folder`, `directory`, `basename`, `key`,
`blob`.

So "the filename contains *germany*" is never accepted as evidence that an event is German.

---

## Value normalisation

`value_maps` in `config/field_mapping.yaml` maps source spellings onto canonical values:

```yaml
value_maps:
  road_type:
    urban: [urban, city, innerstadt, innerorts, built_up]
    autobahn: [autobahn, bab]
  weather:
    rain: [rain, rainy, light_rain, drizzle]
```

An unrecognised value is **preserved**, lower-cased, and surfaced as unmapped — never
silently discarded. Losing a value you did not anticipate is worse than carrying one you
have not yet mapped.

Add a mapping by extending the relevant list and pressing **Reload configuration** on the
Administration page.

---

## Unmapped source fields

Fields no canonical field claimed are kept in `metadata.unmapped` and shown on the Event
Detail → Summary tab. Nothing the source provided is thrown away.

---

## Adding a canonical field

1. Add it to `canonical_fields` in `config/field_mapping.yaml` with its type, whether it is
   required, and its aliases.
2. Add the attribute to `EventMetadata` in `backend/models/contracts.py`.
3. If it is a list, integer, float or datetime, add it to the corresponding coercion set in
   `backend/connectors/normalization.py`.
4. If it needs vocabulary normalisation, add it to `_VOCAB_FIELDS` and give it a
   `value_maps` entry.
5. To export it, add a column to the relevant `config/csv_templates/*.yaml`.

No code change is needed to map a *new source* onto existing canonical fields — that is
configuration, done in the mapping editor.
