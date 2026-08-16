# Production Data Audit

Audit performed before any file was modified, as required by section 3 of the
production-conversion brief.

**Scope:** every file under `backend/` (89 Python modules) and `dashboard/src`.
**Question asked of each hit:** is this (1) legitimate configuration,
(2) test-only data, (3) demo runtime data, (4) a UI placeholder, or
(5) a production default?

**Method:** pattern sweep, then manual classification of every hit, then
*runtime probes* — the classification of the two blocking findings below was
confirmed by executing the code with `AV_MODE=production`, not by reading it.

---

## Summary

| | Count |
|---|---|
| Blocking findings (production would serve fabricated data) | **2** |
| Missing requirements (specified but absent) | **6** |
| Records to remove from the local database | **11,893 rows across 12 tables** |
| Areas audited and found already correct | **7** |

The application is much closer to production-honest than a greenfield
conversion would be: the frontend contains no fabricated data at all, the
backend imports neither `random` nor `faker`, system metrics are real, and the
synthetic adapter already refuses to run in production. The two blocking
findings are both in the *default wiring*, not in the data path — production
honestly processes whatever source it is pointed at; the problem is what it is
pointed at by default.

---

## Blocking findings

### F1 — Production defaults to test fixtures as its event source

**Severity: blocking.** This is the single finding that violates the core rule.

Three independently reasonable decisions combine into the prohibited outcome:

| Location | Setting |
|---|---|
| `backend/settings.py:146` | `local_dataset_dir: Path = PROJECT_ROOT / "tests" / "golden_dataset"` |
| `backend/database/init_db.py:157` | `local_files` connection seeded `enabled: True` |
| `backend/connectors/local_files.py:112` | `dataset_dir = configured or get_settings().local_dataset_dir` |
| `backend/connectors/registry.py` | `default_event_source()` returns the first enabled non-synthetic source |

The `local_files` connection is seeded enabled with `dataset_dir: None`, so it
falls through to the settings default — which points at `tests/golden_dataset`.

**Verified by execution, not inference.** With `AV_MODE=production`:

```
operating_mode      = production
is_production_mode  = True
local_dataset_dir   = D:\Install\support\av-test-automation\tests\golden_dataset
DEFAULT EVENT SOURCE in PRODUCTION = 'local_files'
adapter class = LocalFilesAdapter
adapter reads from = D:\Install\support\av-test-automation\tests\golden_dataset
```

A tester who installs the platform, opens the dashboard and starts a run
without configuring a source gets 22 synthetic golden-dataset events processed
and presented as real results — complete with validation findings, review
queue entries and an exportable CSV. Nothing in the UI would say the data was
synthetic, because as far as every downstream stage is concerned it arrived
from a legitimately configured adapter.

This is precisely what section 5 prohibits: *test fixtures must never be loaded
by the production application*.

Note the synthetic adapter is **not** the problem here — it correctly refuses.
The problem is the local-files adapter being aimed at the fixture directory.

### F2 — Connection status fabricated at seed time

**Severity: blocking (section 8).**

`backend/database/init_db.py` seeds `last_status: "CONFIGURED"` for four
connections — `local_files`, `map_service`, `sensor_store`, `evidence_store` —
at database creation. No probe has run at that point. The status describes an
intention, not an observation.

`CONFIGURED` is not the word `CONNECTED`, so this is less severe than it could
be, but it is still a status the system asserts without evidence, and the
Connections page renders it as though it were a health result.

---

## Missing requirements

| # | Requirement | Spec | State |
|---|---|---|---|
| F3 | Production Readiness page and go-live gate | 95, 96 | absent |
| F4 | `last_successful_connection` and `authentication_status` in the health record | 9 | absent (`last_tested_at` records the *attempt* only) |
| F5 | `docs/DATA_SCOUT_REAL_CONNECTION.md` | 114 | absent |
| F6 | `docs/DATA_SCOUT_FIELD_MAPPING.md` | 115 | absent |
| F7 | `scripts/test_data_scout_connection.py` | 116 | absent |
| F8 | Automated anti-fake test | 100 | absent |

On the section 11 adapter contract: most required operations exist under
different names (`estimate_count` for `count_events`, `get_supported_filters`
for `get_filter_options`, `get_event_bundle` for `get_event`). Renaming a
working, tested interface would be redesign for its own sake, which the brief
forbids. The genuinely absent operations are `get_capabilities`,
`get_prediction_results` and `get_planning_results`; equivalences are recorded
in the field-mapping document rather than churned through the codebase.

---

## Database records requiring removal (section 50)

The local database is **not** empty. It holds the output of golden-dataset test
runs performed during development:

| Table | Rows |
|---|---|
| `detections` | 9,568 |
| `ego_poses` | 2,742 |
| `validation_results` | 1,012 |
| `field_recommendations` | 423 |
| `evidence` | 242 |
| `sensor_streams` | 154 |
| `map_features` | 123 |
| `audit_events` | 106 |
| `events` | 22 |
| `automation_runs` | 6 |
| `reviews` | 1 |
| **Total** | **11,893** |

Provenance is unambiguous — no real source has ever been configured on this
machine, so every row descends from a `local_files`/golden-dataset run. They
are still backed up before removal rather than deleted blind, per section 50.

`connection_profiles` (9) and `configuration_profiles` (7) are **retained**:
those are built-in configuration, not data.

---

## Audited and already correct

These were checked and need no change. Recording them so the conclusion is
falsifiable rather than a claim of general cleanliness.

| Area | Evidence |
|---|---|
| Frontend fabricated data | No `Math.random`, no mock modules, no hard-coded record arrays, no local JSON imports. The three `Promise.resolve`/`setTimeout` hits are a null-guard, an input debounce and a WebSocket reconnect timer. |
| Backend randomness | `random` and `faker` appear nowhere in `backend/`. |
| System health metrics | Real: `psutil.cpu_percent`, `virtual_memory`, `disk_usage`, and `nvidia-smi` probed via `shutil.which` with honest "unavailable" reporting. |
| Dashboard statistics | `home.py` + `analytics.py` issue 32 real database queries; no endpoint returns a literal record set. |
| Synthetic adapter | Confirmed at runtime: raises `DemoDataRefused` under `AV_MODE=production`. |
| Data Scout adapter | Ships `NOT_CONFIGURED` with `base_url: None`. No invented endpoints, no invented auth. |
| Seeded rows | Built-in seeds create configuration only — profiles, connections, CSV templates. No demo events. |

---

## Classification of every pattern hit

| Pattern | Hits | Classification |
|---|---|---|
| `sample` | 145 | (1) legitimate — sensor/pose *sampling* vocabulary (`PoseSample`, `StreamSample`, sample rate). Not demo data. |
| `synthetic` | 44 | (2) test-only — the deliberately gated synthetic adapter and its tests. |
| `demo` | 40 | (1)+(3) the demo-mode gate itself. Legitimate: it is the mechanism that *prevents* fake data in production. |
| `seed` | 8 | (1) built-in configuration seeding (profiles/templates), not demo rows. |
| `placeholder` | 7 | (4) UI input placeholders (form hints). Legitimate. |
| `DEMO_MODE` | 6 | (1) the mode flag. |
| `demoData` | 4 | (1) the `mode.demo` boolean carried to the UI banner. |
| `fixture` | 3 | (2) test-only. |
| `hardcoded` | 1 | (1) a comment in `settings.py` about bandit B104. |
| `fake` | 1 | (1) the string in the refusal message: *"Production mode never substitutes fake data…"*. |
| `random`, `faker`, `Math.random`, `mockData`, `generateFake`, `MOCK_MODE` | 0 | absent |

---

## Conclusion

The data path is honest. The default wiring is not. Fixing F1 and F2 — plus
adding the readiness gate that makes the state visible, and the anti-fake test
that stops it regressing — converts the platform to production-honest without
touching the working AV analysis logic, the dashboard, or deployment.
