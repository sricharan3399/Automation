# Windows Deployment

Everything a tester needs to get the AV Test Automation Dashboard running on an
approved Windows laptop.

---

## New laptop, from scratch

```bash
git clone https://github.com/sricharan3399/Automation.git
cd Automation
SETUP_AND_START.bat
```

Or clone, open the `Automation` folder in Explorer, and double-click
**SETUP_AND_START.bat**.

That is the whole installation. It ends with the dashboard open in your browser
at <http://127.0.0.1:8000>.

### What setup actually does

| Step | Action | Skipped when |
|---|---|---|
| 1 | Validate the workstation | never |
| 2 | Create runtime directories | they already exist |
| 3 | Create `.env` from `.env.example` | `.env` already exists |
| 4 | Create the `.venv` virtual environment | `.venv` already exists |
| 5 | Install Python dependencies | dependency files unchanged |
| 6 | Install dashboard dependencies (`npm ci`) | lockfile unchanged |
| 7 | Build the dashboard (`npm run build`) | dashboard sources unchanged |
| 8 | Initialise the database, generate fixtures | already initialised |
| 9 | Create a desktop shortcut, start, health-check, open browser | `-NoStart` |

Steps 5–8 are keyed on SHA-256 hashes stored in `.runtime/setup_state.json`, so
re-running setup is cheap. It never reinstalls what has not changed.

Nothing in setup contacts a data source.

---

## Everyday use

| Task | Double-click |
|---|---|
| Start | `START_AV_DASHBOARD.bat` |
| Stop | `STOP_AV_DASHBOARD.bat` |
| Update from GitHub | `UPDATE_AND_START.bat` |
| Run the full test suite | `RUN_TESTS.bat` |
| Repair a broken install | `SETUP_AND_START.bat` |

`START_AV_DASHBOARD.bat` performs lightweight checks only. It does **not**
reinstall Python packages, re-run `npm ci`, rebuild the dashboard or reset the
database. If setup has never completed on the machine it says so and stops,
rather than silently doing a first-time install.

The desktop shortcut created during setup points at `START_AV_DASHBOARD.bat`, so
daily use never requires browsing the repository.

---

## Useful switches

```powershell
# Set up but do not launch
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -NoStart

# Force a full reinstall and rebuild
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -Force

# No Node available: install the backend only
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -SkipDashboard

# Start without opening a browser
.\START_AV_DASHBOARD.bat -NoBrowser
```

Execution policy is bypassed **per process** (`-ExecutionPolicy Bypass` on the
command line). The machine's PowerShell policy is never permanently modified.

---

## Ports and binding

The application binds `127.0.0.1:8000` by default. It is deliberately **not**
bound to `0.0.0.0`: this dashboard should not be reachable from every machine on
the LAN.

To change the port, edit `.env`:

```
AV_PORT=8010
```

Every script reads the port from the same place, so the health check, the
browser URL and the port availability check all follow automatically.

---

## Process management

The backend PID is recorded in `.runtime/pids/backend.pid` together with the
process start time, image path and repository root.

`STOP_AV_DASHBOARD.bat` stops **only** a process that still matches all of those.
It never runs anything like `taskkill /IM python.exe /F`, because Windows reuses
PIDs and a tester's other Python or Node work must survive stopping this
dashboard.

If the recorded PID now belongs to something else, the stale PID file is removed
and nothing is killed.

Starting when an instance is already running and healthy simply opens the
browser — it does not launch a second copy.

---

## Updating

```
UPDATE_AND_START.bat
```

1. Verifies git is available and this is a repository.
2. **Stops if tracked source files are modified**, listing them. It never runs
   `git reset --hard` or `git clean` on your behalf.
3. `git fetch origin`, then `git pull --ff-only` — never a surprise merge commit.
4. Stops the running instance before changing anything underneath it.
5. Reinstalls Python or Node dependencies **only if their hashes changed**.
6. Backs up the database to `.runtime/backups/database_<timestamp>.db`, then
   applies the idempotent schema initialisation.
7. Rebuilds the dashboard **only if its sources changed**.
8. Smoke-tests that the updated application imports, then starts it.

If you have local changes you want to keep:

```bash
git stash push -m "before update"     # set aside
git commit -am "local changes"        # or keep them
```

---

## Runtime layout

```
.runtime/                       local only, gitignored
├── logs/
│   ├── setup.log
│   ├── start.log
│   ├── update.log
│   ├── backend.log
│   └── backend.error.log
├── pids/backend.pid
├── backups/database_<timestamp>.db
└── setup_state.json

data/local.db                   this laptop's own database
output/run_<timestamp>/         run artefacts and evidence
.env                            local configuration
```

None of this is committed. Each laptop builds its own.

---

## Corporate proxy

`HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` are honoured by pip, npm and the
application's HTTP client automatically.

TLS verification is **never** disabled to work around a proxy. If your proxy
performs TLS interception, install the corporate root CA into the Windows trust
store and into pip/npm configuration — do not set `verify=False` or
`NODE_TLS_REJECT_UNAUTHORIZED=0`.

---

## Data Scout

The dashboard starts normally whether or not Data Scout is configured. On a new
laptop the connection card reads:

```
NVIDIA / Internal Data Scout

Status:
NOT CONFIGURED

[CONFIGURE]
```

That is expected. No credentials are stored in GitHub. Configure the approved
connection on the **Connections** page, supplying a `credential_key` that names
an entry in the Windows Credential Manager rather than the secret itself:

```powershell
.\.venv\Scripts\python.exe -c "import keyring; keyring.set_password('av-test-automation','AV_DATASCOUT_TOKEN','<token>')"
```

Until then, work against an approved exported dataset with the Local CSV / JSON
connection.

---

## Verifying an installation

```
RUN_TESTS.bat
```

Runs the security audit, backend tests, lint, type checks, dashboard tests and
the production build. Exit codes: `0` healthy, `1` security failure (do not
push), `2` quality failure.

To check the running application directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

```json
{ "status": "healthy", "backend": "healthy", "database": "healthy",
  "dashboard": "built", "version": "1.0.0" }
```

---

## When something fails

Scripts never print a fake PASS. A failure reports the stage, the exact command,
the result and the log path, and states that the application was not started:

```
================================================================
 AV DASHBOARD STARTUP FAILED
================================================================

Stage:    Frontend build
Command:  npm run build
Result:   FAILED
Log:      .runtime\logs\setup.log

The application was not started.
================================================================
```

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
