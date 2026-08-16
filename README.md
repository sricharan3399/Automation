# AV Test Automation Platform

A dashboard-driven, **human-in-the-loop** automation platform for an autonomous-vehicle
test team. It automates the repetitive parts of AV data scouting — query building,
event retrieval, sensor and synchronisation validation, map/lane geometry, timestamp
derivation, evidence generation and CSV production — while keeping every uncertain or
safety-relevant decision with a human reviewer.

The tester never edits YAML, never writes Python, never hand-builds a query, and never
assembles CSV columns by hand. Everything is configured visually.

---

## What it will not do

These are design constraints, not limitations to be worked around later:

| Constraint | Why |
|---|---|
| Machine findings are **candidates**, never confirmed defects | Only a reviewer, or a validated project oracle, may classify a defect |
| Source integrations are **read-only** | The adapter interface exposes no create/update/delete method at all |
| Production submission is **disabled** | There is no one-click submit path in this build |
| An unconfigured Data Scout **fails loudly** | It never fabricates a connection or results |
| Synthetic data is **refused in production mode** | An unavailable source fails the run; it does not silently fall back to fake data |
| Rules needing an approved project threshold ship **disabled** | The platform does not invent a safety-relevant threshold |
| A skipped rule is **not** a passed rule | "We could not check this" and "we checked and it's fine" never look the same |
| Redaction is **fail-closed** | If redaction cannot be applied, the export is refused |

---

## Quick start (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

Then:

```powershell
.\.venv\Scripts\python.exe launcher.py
```

The dashboard opens at <http://localhost:8000>. The API documentation is at
<http://localhost:8000/api/docs>.

Opening the application never starts a data-source query.

### Manual install

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m backend.cli init-db
python tests/golden_dataset/generate.py
cd dashboard && npm install && npm run build && cd ..
.venv\Scripts\python.exe launcher.py
```

Python 3.10+ is supported; 3.11+ is recommended. Node 18+ is needed only to build the
dashboard — the API works without it.

---

## Architecture

```
        Tester (browser)
               │
     React + TypeScript dashboard          dashboard/
               │  REST + WebSocket
        FastAPI application                backend/main.py
               │
   ┌───────────┴───────────────────────────────────┐
   │                                               │
Connection manager                          Run manager
backend/connectors/                         backend/workers/
   │                                               │
DataScoutAdapter (read-only interface)      Per-event pipeline
   ├── NvidiaInternalDataScoutAdapter        backend/pipeline/orchestrator.py
   ├── LocalFilesAdapter (CSV / JSON)               │
   └── SyntheticAdapter (DEMO ONLY)                 │
                                                    ▼
        synchronization → geometry → vision → behavior
                            │
                    validation rules
                            │
                confidence  →  auto-prefill
                            │
             evidence  ──────┴──────  reports
                            │
                   Human review queue
                            │
              CSV + JSON + evidence + QA report
                            │
                     Audit trail (append-only)
```

Every module communicates through **versioned JSON contracts**
(`backend/models/contracts.py`), so each stage is independently testable and a stored run
can be replayed against a later version with a clear compatibility signal.

---

## The dashboard

| # | Page | What it does |
|---|---|---|
| 1 | Home | Connection status, current configuration, previous run, quick actions |
| 2 | Connections | Configure, test, discover schema, edit the field mapping |
| 3 | Scout Setup | The visual query builder with a live matching-record count |
| 4 | Scenario Builder | Bus scenario tags and ego-lane relationships |
| 5 | Sensor Configuration | Per-stream Required / Optional / Ignore |
| 6 | Map & Lane Setup | Junction candidates, polygon assessment, entry/exit edges, markers |
| 7 | Validation Rules | The rule catalogue, thresholds, and the confidence routing policy |
| 8 | Automation Runs | Run history, pause/resume/cancel, resumable checkpoints |
| 9 | Live Processing | Live pipeline stages over a WebSocket |
| 10 | Event Explorer | Searchable, virtualised event table with a full detail drawer |
| 11 | Review Queue | Side-by-side original / recommendation / confidence / reviewer value |
| 12 | Evidence Viewer | Evidence with hashes, redaction state and stated unavailability |
| 13 | CSV / Reports | Template and column selection, export readiness, preview, download |
| 14 | Quality Analytics | Error breakdown, review quality, performance, sensor quality |
| 15 | Configuration Profiles | Save and load complete run configurations |
| 16 | Audit Logs | The append-only trail |
| 17 | System Health | Live resources and the environment checks |
| 18 | Administration | Effective configuration, roles, retention, reload |

The map view is a **self-contained SVG** drawn in the event's local metric frame. No tile
server is contacted, so no positional data can leave the workstation.

---

## Connecting a data source

Three integration paths ship in this build:

1. **NVIDIA / Internal Data Scout** (`nvidia_internal_data_scout`) — a complete, generic,
   configurable REST client. **No NVIDIA-proprietary endpoint or payload shape is invented
   anywhere in this repository**, because none has been supplied. The base URL,
   authentication mode, endpoint paths, pagination style and response field paths are all
   configuration, entered on the Connections page. Until they are supplied, every method
   raises and the connection reports `NOT_CONFIGURED`.

2. **Local CSV / JSON** (`local_files`) — reads approved exported event bundles from a
   directory. This is a legitimate production path when the approved route is
   *Data Scout → approved export → workstation*, and it is what the test suite uses.

3. **Synthetic** (`synthetic`) — deterministic demo data, refused unless `AV_MODE=demo`.

See [docs/DATA_SCOUT_INTEGRATION.md](docs/DATA_SCOUT_INTEGRATION.md) for the exact
configuration required, and [docs/FIELD_MAPPING.md](docs/FIELD_MAPPING.md) for schema
discovery and the mapping editor.

---

## Confidence and human review

Confidence is tracked **per field**, never as one global number:

| Band | Range | Behaviour |
|---|---|---|
| `auto_confirm` | 95–100% | Prefilled; quick reviewer confirmation |
| `verify` | 80–95% | Prefilled; mandatory verification |
| `suggest` | 50–80% | Ranked recommendations shown; **not** auto-selected |
| `manual` | below 50% | Left blank; manual entry required |

Two rules sit above the bands:

* A value below the configured hard floor is never auto-selected, whatever the band says.
* A disagreement on a **safety-critical** field routes the record to senior review
  regardless of confidence.

An exported CSV cell therefore always means *a human accepted this*, or *the machine was
confident enough that the policy permits it* — never *the machine's best guess, unlabelled*.

Every confidence value carries its arithmetic. Click it in the dashboard to see the
components, the weights actually used, and which evidence was missing. Missing evidence is
never counted as agreement: weights are renormalised over the components that were
genuinely available.

---

## Run outputs

```
output/run_<timestamp>/
├── results.csv              # only written when export readiness is clean
├── results_partial.csv      # written instead when some rows are blocked
├── rejected_records.csv     # every blocked row, with the rule and a correction
├── summary.json
├── validation_report.json   # per-event rule outcomes, including what was skipped
├── run_config.json          # the frozen configuration, for reproducibility
├── evidence_manifest.csv
├── audit.jsonl
└── evidence/<event_ref>/
    ├── map_trajectory.svg
    ├── telemetry_summary.json
    ├── validation_warnings.json
    └── final_review.json
```

Nothing is silently dropped. A record that cannot be exported appears in
`rejected_records.csv` with the rule that rejected it and a recommended correction.

---

## Command line

```bash
av-scout start          # start the backend and open the dashboard
av-scout check          # environment checks
av-scout init-db        # create the schema and seed built-in profiles
av-scout connections    # test every configured connection
av-scout run --country DE --object bus --preview-only
av-scout run --country DE --object bus --execute --limit 20
```

---

## Development

```bash
pytest                       # 190 backend tests
ruff check .                 # lint
mypy                         # type check
cd dashboard && npm test     # dashboard tests
cd dashboard && npm run build
python tests/golden_dataset/generate.py   # regenerate the synthetic fixtures
```

The golden dataset is **synthetic and deterministic**: 25 fixtures covering easy cases,
difficult cases, every intersection category and every blocking validation rule. It is
generated from `backend/connectors/synthetic.py`, and CI fails if a regeneration produces
a different result. No production AV data is ever committed.

---

## Security posture

* Secrets are resolved from the OS credential store or an injected environment variable.
  They are never stored in the database, never in a configuration profile, never returned
  by the API, and the API rejects any attempt to save one.
* Internal identifiers are replaced by salted, non-reversible pseudonyms before anything
  leaves the approved environment.
* Precise coordinates are reduced; exported text is scanned for credentials, tokens,
  e-mail addresses, host paths, plates and VINs.
* Geometry is stored and rendered in a local metric frame, so exports carry no global
  position.
* CI runs entirely offline against the synthetic dataset and can never reach production
  AV data.

Full detail: [docs/SECURITY.md](docs/SECURITY.md).

---

## Documentation

| Document | Contents |
|---|---|
| [DATA_SCOUT_INTEGRATION.md](docs/DATA_SCOUT_INTEGRATION.md) | Connecting a real source; what is still required |
| [DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md) | Every page and control |
| [TESTER_WORKFLOW.md](docs/TESTER_WORKFLOW.md) | The day-to-day workflow |
| [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Configuration, roles, retention |
| [SECURITY.md](docs/SECURITY.md) | Secrets, redaction, data handling |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Workstation and enterprise deployment |
| [FIELD_MAPPING.md](docs/FIELD_MAPPING.md) | Schema discovery and canonical fields |
| [VALIDATION_RULES.md](docs/VALIDATION_RULES.md) | The rule catalogue |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common problems |

---

## Status

Everything in this repository runs today against the local adapter and the synthetic
golden dataset. The **NVIDIA / Internal Data Scout connection is not integrated**: the
adapter is implemented and tested, but no approved endpoint, authentication mode or schema
has been supplied, so it reports `NOT_CONFIGURED` and refuses to operate. Supplying that
configuration is the remaining step — see
[docs/DATA_SCOUT_INTEGRATION.md](docs/DATA_SCOUT_INTEGRATION.md).
