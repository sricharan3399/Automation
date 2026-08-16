# GitHub Setup

How this repository is wired to GitHub, and how to work with it safely.

**Repository:** <https://github.com/sricharan3399/Automation.git>
**Default branch:** `main`

---

## Cloning

```bash
git clone https://github.com/sricharan3399/Automation.git
cd Automation
SETUP_AND_START.bat
```

That is the entire second-laptop procedure. See
[DEPLOYMENT_WINDOWS.md](DEPLOYMENT_WINDOWS.md) for what setup does.

---

## What is and is not in the repository

**Committed** — everything needed to build and run the platform:

| | |
|---|---|
| `backend/` | FastAPI application and the AV analysis engines |
| `dashboard/src/` | React + TypeScript sources |
| `config/` | Taxonomy, validation rules, thresholds, CSV templates |
| `tests/` | Test suites and the synthetic golden dataset |
| `scripts/`, `*.bat` | Windows deployment automation |
| `docs/` | Documentation |
| `.env.example` | Field names only, never values |

**Never committed** — enforced by `.gitignore` and proven by the audit tool:

| | |
|---|---|
| `.env` | Local configuration |
| `data/*.db` | Each laptop initialises its own database |
| `output/` | Run artefacts, CSVs, evidence |
| `.runtime/` | Logs, PIDs, backups, setup state |
| `.venv/`, `node_modules/`, `dashboard/dist/` | Build products |
| Raw AV media | `*.mp4 *.bag *.mcap *.pcap *.las *.parquet` and more |
| Credentials | `*.pem *.key *.p12 credentials.* secrets.*` |

The one committed dataset is `tests/golden_dataset/` — 25 **synthetic**,
deterministic fixtures generated from `backend/connectors/synthetic.py`, each
stamped `is_synthetic: true` and naming the defects deliberately injected into
it. CI regenerates them and fails if the output differs.

---

## Before every push

```
RUN_TESTS.bat
```

Or directly:

```powershell
.\.venv\Scripts\python.exe scripts\repository_audit.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_repository.ps1
```

### The audit tool

`scripts/repository_audit.py` scans for secrets, environment files, raw AV data,
runtime databases, generated output, oversized files and credential-bearing
filenames.

Three properties worth knowing:

* **It classifies against git, not guesswork.** Findings are checked with
  `git check-ignore`, so the audit can never disagree with what `git add` would
  actually stage. An ignored `.db` is reported as `IGNORED` and does not block.
* **It never prints secret values.** Findings name the file, the line and the
  rule; the matched text is redacted.
* **Placeholders are not secrets.** `.env.example` carries field names with
  empty values by design and is not flagged.

```
=================================================
REPOSITORY SECURITY AUDIT
=================================================

Files scanned:                 483
Git ignore rules applied:      YES

Secrets Detected:              0
Environment Files:             REVIEW
AV Raw Data Files:             0
Runtime Databases:             3 IGNORED
Generated Results:             282 IGNORED
Large Files:                   0
Suspicious Credentials:        0

=================================================
Repository Safe to Commit:
YES
=================================================
```

Exit codes: `0` safe, `1` unsafe, `2` the audit failed to run.
`--staged` audits only staged files; `--json` emits machine-readable output.

---

## Authentication

Pushing needs a GitHub credential. **Never put a token in a file in this
repository** — not in `.env`, a `.bat`, a `.ps1`, a Python file, the README, or
the remote URL.

Use one of:

1. **Git Credential Manager** (ships with Git for Windows, recommended). The
   first push opens a browser sign-in and the credential is stored in the
   Windows Credential Manager:

   ```bash
   git config --global credential.helper manager
   git push -u origin main
   ```

2. **GitHub CLI**

   ```bash
   gh auth login
   gh auth status
   ```

3. **SSH**

   ```bash
   git remote set-url origin git@github.com:sricharan3399/Automation.git
   ```

To clear a stored credential: Windows *Credential Manager → Windows Credentials*,
remove the `git:https://github.com` entry.

---

## Branch and remote conventions

```bash
git remote -v          # must be https://github.com/sricharan3399/Automation.git
git branch --show-current
```

* Work on `main` for this deployment, or a feature branch merged via PR.
* **Never force push.** `git push --force` is not used anywhere in this project.
  If histories conflict, stop and resolve deliberately.
* **Never `git reset --hard` or `git clean` automatically.** `UPDATE_AND_START.bat`
  stops and lists your modified files rather than discarding them.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | Steps |
|---|---|
| `backend` | ruff, mypy, golden-dataset determinism, pytest, headless dry run, headless full run with output verification, tracked-secret check |
| `dashboard` | typecheck, lint, vitest, production build |
| `security` | `pip-audit`, `bandit`, committed-AV-media check |

CI runs on GitHub-hosted runners, which **must never reach production AV data**.
The workflow forces `AV_MODE=production`, `AV_DATASCOUT_ENABLED=false` and
`AV_ALLOW_PRODUCTION_SUBMISSION=false`, and every job runs offline against the
synthetic dataset.

Connecting CI to an internal source would need an approved self-hosted runner
and is deliberately not configured.

---

## Releasing

Follow [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). Bump `VERSION` and
`application.software_version` in `config/base.yaml` together — every run records
the version that produced it, so they must agree.
