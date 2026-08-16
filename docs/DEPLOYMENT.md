# Deployment

## Target

```
Approved work laptop / workstation
        │
Corporate network / VPN
        │
Approved internal data sources
        │
NVIDIA / Internal Data Scout
        │
Automation backend (localhost)
        │
Local dashboard (localhost:8000)
```

Everything runs on the workstation. Nothing is exposed to the network by default: the
backend binds `127.0.0.1`.

---

## Workstation install (primary)

**Requirements**: Windows 10/11, Python 3.10+ (3.11+ recommended), 4 GB RAM, 5 GB free
disk. Node 18+ only to build the dashboard. An NVIDIA GPU is optional — metadata,
geometry, validation and export are all CPU work.

```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

The installer validates Python, creates `.venv`, installs dependencies, creates `data/` and
`output/`, copies `.env.example` to `.env`, runs the environment checks, initialises the
database, generates the golden dataset, builds the dashboard and creates a desktop
shortcut.

Useful switches: `-SkipDashboard` (no Node available), `-SkipTests` (runtime dependencies
only), `-NoShortcut`, `-Start`.

Start with the shortcut, or:

```powershell
.\.venv\Scripts\python.exe launcher.py
```

### Startup sequence

Every launch checks the application version, the database, the configuration, disk and GPU,
and whether credentials are available, then starts the services and opens the dashboard.

It does **not** start a Data Scout query. Opening the application must never touch
production data.

---

## Configuration

`.env` (gitignored) or real environment variables. Environment always wins, so a corporate
secret injector cannot be shadowed by a stale local file.

| Variable | Default | Purpose |
|---|---|---|
| `AV_HOST` / `AV_PORT` | `127.0.0.1` / `8000` | Bind address |
| `AV_MODE` | `production` | `production` refuses synthetic data |
| `AV_DATABASE_URL` | `sqlite:///./data/local.db` | SQLite or PostgreSQL |
| `AV_LOCAL_USER` / `AV_LOCAL_ROLE` | `local.tester` / `tester` | Identity for RBAC and audit |
| `AV_LOCAL_DATASET_DIR` | `./tests/golden_dataset` | Local adapter source |
| `AV_DATASCOUT_ENABLED` | `false` | Data Scout adapter |
| `AV_DATASCOUT_BASE_URL` | — | Approved API root |
| `AV_DATASCOUT_TOKEN` | — | Prefer the OS credential store |
| `AV_ALLOW_PRODUCTION_SUBMISSION` | `false` | Keep false |
| `AV_ALLOW_BROWSER_AUTOMATION` | `false` | Keep false |
| `AV_SOURCE_ACCESS_MODE` | `read_only` | Keep read-only |
| `AV_REDACTION_SALT` | generated | Pseudonymisation salt |

The Administration pages need `AV_LOCAL_ROLE=administrator`. The default is `tester`
because the secure default is the one that grants least.

---

## Database

### SQLite (default)

Zero administration. WAL mode, a busy timeout and foreign keys are enabled so the API and
the background run worker share the file safely. Good for a single-operator workstation.

Back up by copying `data/local.db` while the application is stopped.

### PostgreSQL + PostGIS (enterprise)

```bash
AV_DATABASE_URL=postgresql+psycopg://av_user:***@db.internal:5432/av_automation
pip install -e ".[postgres]"
python -m backend.cli init-db
```

Every column type used works on both engines, so no code changes are required.

Geometry is stored as GeoJSON-shaped JSON for SQLite portability. For spatial queries at
enterprise scale, add a generated `geography` column and a GiST index by migration:

```sql
ALTER TABLE map_features
  ADD COLUMN geom geography(Geometry, 4326)
  GENERATED ALWAYS AS (ST_GeomFromGeoJSON(geometry::text)) STORED;
CREATE INDEX idx_map_features_geom ON map_features USING GIST (geom);
```

Application code is unaffected — it reads the JSON column either way.

---

## Multi-user deployment

The platform is designed as a local desktop tool. If it is served to several testers:

1. Put it behind an approved authenticating reverse proxy.
2. Have the proxy forward `X-AV-User` and `X-AV-Role`; those take precedence over the local
   configuration.
3. Use PostgreSQL, not SQLite.
4. Ensure `output/` is on storage covered by the approved data-handling policy — it holds
   evidence and audit trails.
5. Do not expose the port directly. RBAC governs what an authenticated operator may do; it
   is not a substitute for authentication.

---

## Run outputs and retention

Each run writes a self-contained directory under `output/run_<timestamp>/`. Evidence stays
local; only redacted, pseudonymised exports are intended to leave the environment.

The Administration → Retention view **reports** what a retention policy would cover. It
does not delete. Deletion of AV records is deliberately never automatic.

---

## Upgrading

1. Stop the application.
2. Back up `data/local.db` and any `output/` directories you still need.
3. Pull the new version.
4. `pip install -r requirements-dev.txt`
5. `python -m backend.cli init-db` — idempotent; existing rows and edited built-in profiles
   are left untouched.
6. Rebuild the dashboard: `cd dashboard && npm install && npm run build`
7. Start and check `/api/v1/health` for the new version.

Every run records the software, contract, rule, model and map versions that produced it, so
an upgrade never makes an earlier result ambiguous.

---

## CI

`.github/workflows/ci.yml` runs lint, type check, unit and integration tests, a golden
dataset determinism check, a headless dry run, a headless full run with output
verification, dashboard type-check/lint/test/build, and a security scan.

It runs entirely offline against the synthetic dataset. Cloud-hosted CI **must never**
reach production AV data, so `AV_MODE=production` and `AV_DATASCOUT_ENABLED=false` are
forced in the workflow environment.

### Self-hosted runner (optional, disabled by default)

If integration testing against an internal source is ever approved:

```
GitHub → approved self-hosted corporate runner → internal environment → Data Scout
```

This is not configured here. Enabling it requires approval, a dedicated runner inside the
approved network, and credentials from the corporate secret manager — never repository
secrets.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `The dashboard has not been built yet` | `cd dashboard && npm install && npm run build`, then restart. The API still works at `/api/docs`. |
| Data Scout shows `NOT_CONFIGURED` | Expected until approved details are supplied. See [DATA_SCOUT_INTEGRATION.md](DATA_SCOUT_INTEGRATION.md). |
| `403` on Administration | Set `AV_LOCAL_ROLE=administrator` and restart. |
| `Synthetic data is refused` | Correct in production mode. Set `AV_MODE=demo` deliberately, or connect an approved source. |
| Local adapter finds no events | Check `AV_LOCAL_DATASET_DIR`. Expects `events/*.json`, `*.json`, or `*.csv`. |
| Port 8000 in use | `AV_PORT=8010`, or `launcher.py start --port 8010`. |
| Run stuck at `PENDING` | Check the log. A run thread that fails sets `FAILED` with the reason. |
| `CSV NOT READY` | Open the readiness panel: each blocking issue names its rule and a correction. |
| Evidence images unavailable | Expected — stream manifests reference frames but do not carry pixels. Configure an approved frame provider to enable image evidence. |
