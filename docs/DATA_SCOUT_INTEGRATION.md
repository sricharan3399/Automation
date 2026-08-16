# Data Scout Integration

## Current status: NOT CONFIGURED

The NVIDIA / Internal Data Scout adapter is **implemented and tested, but not connected**.

No approved endpoint, authentication flow or schema has been supplied, so none has been
guessed. Searching this repository for an NVIDIA-specific URL, payload shape or header
will find nothing, because nothing was invented. Every method of
`NvidiaInternalDataScoutAdapter` raises `AdapterNotConfigured`, and the Connections page
reports `NOT_CONFIGURED` with the list of what is missing.

This is deliberate. An adapter that pretended to work would produce results a tester might
act on.

---

## What is still required

To activate the connection, an administrator needs the following from the team that owns
Data Scout. Everything is entered on the **Connections** page and stored in the connection
profile; nothing needs a code change.

### 1. Endpoint and authentication

| Item | Example | Notes |
|---|---|---|
| `base_url` | `https://datascout.internal.example/api/v2` | The approved API root |
| `auth_mode` | `bearer` \| `api_key` \| `oauth_client_credentials` \| `none` | |
| `credential_key` | `AV_DATASCOUT_TOKEN` | Names the OS credential-store entry or injected environment variable. **The secret value itself is never entered here and never stored.** |
| `api_key_header` | `X-API-Key` | Only for `api_key` mode |
| `verify_tls` | `true` | Leave true unless an approved internal CA requires otherwise |

`oauth_client_credentials` additionally needs the approved token endpoint, client id and
scope. Until those are supplied the adapter refuses that mode explicitly rather than
guessing a token flow.

### 2. Endpoint paths

At minimum:

```json
{
  "endpoints": {
    "search_events":   "/events/search",
    "event_metadata":  "/events/{event_id}"
  }
}
```

Optional, each unlocking a capability:

| Key | Unlocks |
|---|---|
| `projects`, `datasets` | Project and dataset dropdowns |
| `supported_filters` | Source-derived filter vocabulary (otherwise the bundled fallback is used and labelled as such) |
| `schema` | Schema discovery without sampling records |
| `count` | Exact record estimates on Scout Setup |
| `sensor_manifest` | Sensor availability and synchronisation analysis |
| `trajectory` | Geometry, timestamps and behaviour analysis |
| `map_context` | Junction selection, polygon, entry/exit edges, distance markers |
| `perception_results` | Perception and tracking analysis |
| `annotations` | Missed detections, false positives, classification errors |
| `health` | A cheaper connection test than a search |

A capability whose endpoint is absent is reported as unavailable with that reason. It is
never silently skipped.

### 3. Response field paths

Where the useful values sit inside a response body, as dotted paths:

```json
{
  "response_paths": {
    "event_list":      "data.results",
    "event_id_field":  "id",
    "next_cursor":     "data.paging.next",
    "total_count":     "data.paging.total",
    "event_metadata":  "data",
    "sensor_manifest": "data.streams",
    "trajectory":      "data.poses",
    "map_context":     "data.map",
    "api_version":     "meta.version",
    "permissions":     "meta.permissions"
  }
}
```

### 4. Query translation

Which source parameter each dashboard filter maps to:

```json
{
  "query_translation": {
    "country_code":  "country",
    "object_type":   "classes",
    "road_type":     "road_class",
    "start_date":    "from",
    "end_date":      "to"
  }
}
```

A filter with no entry here is **applied locally after retrieval** rather than dropped, and
the query preview lists which filters that applies to. A filter is never silently ignored.

### 5. Pagination style

```json
{ "page_param": "cursor", "limit_param": "limit" }
```

---

## If the real interface is not REST

`NvidiaInternalDataScoutAdapter` implements REST only, and says so: selecting any other
integration type puts `integration_type '<x>' requires a dedicated adapter` in the missing
-configuration list.

For an SDK, GraphQL, CLI or database interface, add a sibling class implementing
`backend.connectors.base.DataScoutAdapter` and register it in
`backend/connectors/registry.py`. Nothing above the adapter layer changes: the pipeline,
rules, review queue and exports all work against the interface, not the transport.

The interface is deliberately **read-only** — it exposes no method that could create,
modify or delete anything in a source system.

---

## Integration priority

The platform follows the approved priority order:

1. Approved internal API ← `NvidiaInternalDataScoutAdapter`
2. Approved SDK / data-access layer ← add a sibling adapter
3. Approved exported JSON/CSV ← `LocalFilesAdapter`, working today
4. Approved network/service integration
5. Browser automation ← **not implemented**, and disabled by configuration

Browser automation is deliberately absent from this build. If policy later permits it, the
approved approach is Playwright with `data-testid`/ARIA/semantic selectors — never
screen-coordinate clicking — in read-only mode by default.

---

## Verifying a new connection

1. **Connections → Configure**: enter the settings above. Secrets are refused here; supply
   a `credential_key` instead.
2. **Test**: expect `CONNECTED` with a latency and API version. Any failure states what to
   correct.
3. **Discover Schema**: inspects the fields the source actually returns and proposes a
   mapping onto canonical fields, each with a confidence and the method used.
4. **Review the mapping**: correct anything wrong, then Save. See
   [FIELD_MAPPING.md](FIELD_MAPPING.md).
5. **Scout Setup**: the dropdowns should now show `source` rather than `fallback`.
6. **Dry run** with a small limit. It writes nothing.
7. **Full run** once the dry run looks right.

---

## Working before the connection exists

The platform is fully usable today:

* **Local CSV / JSON adapter** against an approved exported dataset. Point
  `AV_LOCAL_DATASET_DIR` at it, or set `dataset_dir` on the connection.
* **DEMO MODE** (`AV_MODE=demo`) for a synthetic walkthrough. Every record is stamped
  synthetic and the dashboard shows a DEMO badge.

In production mode, an unavailable source **fails the run** with an actionable message and
a saved checkpoint. It never substitutes synthetic data.

---

## Local event bundle format

The local adapter reads `av-scout-local-event/1.0` documents:

```jsonc
{
  "schema": "av-scout-local-event/1.0",
  "metadata": { "event": "EVT-0001", "country_code": "DE", "...": "raw source fields" },
  "streams":  [ { "stream_type": "camera", "camera_position": "front_main",
                  "nominal_rate_hz": 10.0,
                  "samples": [ { "t": 0.0, "signature": "..." } ] } ],
  "poses":    [ { "t": 0.0, "x_m": 0.0, "y_m": -250.0, "heading_rad": 1.57,
                  "speed_mps": 13.9, "localization_quality": 0.96 } ],
  "detections": [ { "t": 3.9, "source": "perception", "object_type": "bus",
                    "track_id": "T-001", "bounding_box": {"x":0.4,"y":0.4,"w":0.2,"h":0.2},
                    "confidence": 0.93 } ],
  "map_context": { "map_version": "hdmap-2026.06-de", "features": [ /* GeoJSON-shaped */ ] },
  "reference_data_available": true,
  "source_start_t": 0.0,
  "source_end_t": 22.1
}
```

Only `metadata` is required. Every other section is reported as unavailable when absent —
never synthesised. Coordinates are in the event's local metric frame (x=east, y=north,
metres), which is what keeps global positions out of exports.

Layout: `<dir>/events/*.json` (preferred, and authoritative when present), or `<dir>/*.json`,
or `<dir>/*.csv` for metadata-only rows.
