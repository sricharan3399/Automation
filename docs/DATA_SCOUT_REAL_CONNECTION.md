# Connecting Data Scout for real

The platform ships with the Data Scout adapter **NOT CONFIGURED**, and that is
the correct state. No endpoint, authentication scheme or field name has been
guessed, invented or reverse-engineered. Until the approved details below are
supplied, the adapter refuses every operation rather than fabricating a
connection or results.

This document is the checklist to take to whoever owns Data Scout access in
your project.

---

## What you need to bring back

Answer these 25 questions. Anything you cannot answer, leave blank — a blank is
useful information, and the platform will report the capability as
`NOT SUPPORTED BY SOURCE` rather than pretending.

### Access mechanism

| # | Question | Answer |
|---|---|---|
| 1 | What is Data Scout's actual access mechanism? | |
| 2 | REST / SDK / GraphQL / database / CLI / export? | |
| 3 | Base URL (if applicable) | |
| 4 | Authentication mechanism | |
| 5 | Project identifier | |
| 6 | Dataset identifier | |

Only `rest_api` is implemented today. The adapter interface is written so that
`sdk`, `graphql`, `cli`, `database`, `csv_export` and `json_export` can each be
added as a sibling class without touching the pipeline — but the class for your
mechanism must exist before the platform can talk to it, and it refuses rather
than approximating.

### Query surface

| # | Question | Answer |
|---|---|---|
| 7 | Search endpoint / method | |
| 8 | Count endpoint / method (or: is counting unsupported?) | |
| 9 | Pagination method (cursor, offset, page token?) | |
| 23 | Rate limits (requests/second, burst, `Retry-After` behaviour) | |
| 24 | Read permissions granted to your account | |
| 25 | Any streaming or subscription capability | |

If there is no count endpoint, the dashboard shows
`COUNT NOT AVAILABLE UNTIL QUERY EXECUTION`. That is the honest answer and it is
supported — do not invent an estimate to fill the box.

### Field names

| # | Question | Answer |
|---|---|---|
| 10 | Country field | |
| 11 | Object field | |
| 12 | Scenario field | |
| 13 | Road-type field | |
| 14 | Lane information | |
| 15 | Timestamp format | |
| 16 | Event identifier | |
| 17 | Session identifier | |

Record these in [DATA_SCOUT_FIELD_MAPPING.md](DATA_SCOUT_FIELD_MAPPING.md).

### Per-event retrieval

| # | Question | Answer |
|---|---|---|
| 18 | Sensor-manifest retrieval | |
| 19 | Trajectory retrieval | |
| 20 | Map-data retrieval | |
| 21 | Annotation retrieval | |
| 22 | Perception-result retrieval | |

Anything unavailable degrades honestly: a missing sensor manifest yields
`MISSING` streams, an absent trajectory disables the geometry stage for that
event, and absent reference annotations disable perception comparison entirely
(the platform will *not* claim `MISSED DETECTION` without ground truth).

---

## What must not be done to obtain these answers

Stated explicitly because the temptation is real when a deadline is close:

- Do not guess or probe for endpoint URLs.
- Do not reverse-engineer authorization, or replay a token captured from a
  browser session.
- Do not scrape credentials out of another tool's configuration.
- Do not disable TLS verification (`verify=False`,
  `NODE_TLS_REJECT_UNAUTHORIZED=0`) to get past a proxy error.
- Do not use an undocumented internal endpoint without written authorization.

If access is refused, the correct outcome is that this adapter stays
`NOT CONFIGURED`. That is a supported, fully functional state — the rest of the
platform works, and the Production Readiness page reports exactly why it is not
production-ready.

---

## Where the answers go

Configuration lives on **Connections → NVIDIA / Internal Data Scout →
Configure**, and is stored in `connection_profiles.settings_json`. Nothing
secret is stored there.

```jsonc
{
  "enabled": true,
  "base_url": "https://<approved-host>/api/v1",
  "auth_mode": "bearer",              // none | bearer | api_key | oauth_client_credentials
  "integration_type": "rest_api",
  "verify_tls": true,                  // never set false
  "timeout_seconds": 60,
  "page_param": "cursor",
  "limit_param": "limit",

  // Required. The adapter refuses to operate without both.
  "endpoints": {
    "search_events":  "/events/search",
    "event_metadata": "/events/{event_id}",

    // Optional - omit any the source does not provide.
    "count":              "/events/count",
    "projects":           "/projects",
    "datasets":           "/datasets",
    "supported_filters":  "/filters",
    "schema":             "/schema",
    "sensor_manifest":    "/events/{event_id}/streams",
    "trajectory":         "/events/{event_id}/trajectory",
    "map_context":        "/events/{event_id}/map",
    "annotations":        "/events/{event_id}/annotations",
    "perception_results": "/events/{event_id}/perception",
    "health":             "/health"
  },

  // Where the payload actually keeps things, when it is not at the top level.
  "response_paths": {
    "search_events": "data.results",
    "count":         "data.total"
  },

  // Canonical filter name -> the source's query parameter name.
  "query_translation": {
    "country_code": "country",
    "object_types": "objectClass"
  }
}
```

### The credential

The token is **never** written to `settings_json`, `.env`, a BAT file, the
repository, logs or the audit trail. Store it in the Windows Credential
Manager:

```bash
.venv\Scripts\python.exe -c "import keyring; keyring.set_password('av-test-automation','AV_DATASCOUT_TOKEN','<token>')"
```

Or have the approved secret injector export `AV_DATASCOUT_TOKEN` into the
process environment at launch. The adapter resolves it lazily at request time.

---

## Verifying, in the right order

Do not point the platform at thousands of events on day one. Sections 117–120
of the brief define the escalation, and it exists to catch a wrong field
mapping while it is still cheap:

```bash
.venv\Scripts\python.exe scripts\test_data_scout_connection.py
```

That performs authentication, a capability read, a schema read and one small
metadata query — nothing destructive, nothing bulk. Then:

1. **5 events**, metadata only — compare every field against Data Scout by hand.
2. **20 events**, dry run — confirm filters and validation behave.
3. **CSV for those 20** — verify event IDs, country, scenario, road, lanes,
   timestamps and warnings against the source manually.
4. **100 events**, batch — measure accuracy and throughput.
5. Larger batches, then realtime/incremental if the source supports it.

Only after step 3 has been checked by a human should a larger batch run.
