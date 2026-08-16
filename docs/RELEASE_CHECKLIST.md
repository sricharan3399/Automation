# Release Checklist

Work top to bottom. Anything unchecked in the **Security** section blocks the
release outright — those are not judgement calls.

---

## Security (blocking)

- [ ] `scripts/repository_audit.py` exits 0 — **Repository Safe to Commit: YES**
- [ ] No API keys, tokens, passwords or client secrets anywhere in the tree
- [ ] `.env` is not committed (`git check-ignore -q .env` succeeds)
- [ ] No production AV data committed — no `.bag .mcap .pcap .las .mp4 .parquet`
- [ ] No production CSVs committed — `output/` is ignored
- [ ] No local database committed — `data/local.db` is ignored
- [ ] No confidential evidence committed — evidence stays under `output/`
- [ ] No credential files — `.pem .key .p12 credentials.* secrets.*`
- [ ] No file exceeds the 10 MB threshold without a deliberate decision
- [ ] `.env.example` contains field names only, with no populated values

## Quality

- [ ] Backend tests pass — `pytest`
- [ ] Backend lint passes — `ruff check .`
- [ ] Backend types pass — `mypy`
- [ ] Dashboard tests pass — `npm test`
- [ ] Dashboard lint passes — `npm run lint`
- [ ] Dashboard builds — `npm run build` produces `dashboard/dist/index.html`
- [ ] Golden dataset is deterministic — regenerating produces no diff

All of the above in one command: `RUN_TESTS.bat`

## Deployment

- [ ] Clean-laptop deployment tested from a fresh clone
- [ ] `SETUP_AND_START.bat` completes and opens the dashboard
- [ ] `START_AV_DASHBOARD.bat` starts **without** reinstalling anything
- [ ] `STOP_AV_DASHBOARD.bat` stops only this application's processes
- [ ] `UPDATE_AND_START.bat` fetches, rebuilds only what changed, and restarts
- [ ] `UPDATE_AND_START.bat` refuses to run with uncommitted tracked changes
- [ ] `/health` returns JSON with `"status": "healthy"`
- [ ] The dashboard is reachable at the configured port and binds `127.0.0.1`

## Application behaviour

- [ ] The dashboard starts with Data Scout **NOT CONFIGURED** and stays usable
- [ ] A dry run writes nothing and exports nothing
- [ ] A full run produces `results.csv`, `summary.json`, `validation_report.json`,
      `evidence_manifest.csv` and `audit.jsonl`
- [ ] Re-running the same events upserts rather than duplicating
- [ ] Export is refused while blocking errors remain
- [ ] Production submission is still **DISABLED**

## Git

- [ ] `git remote -v` is `https://github.com/sricharan3399/Automation.git`
- [ ] Branch is `main`
- [ ] `git status` is clean after committing
- [ ] Local and remote HEAD hashes match after pushing
- [ ] No force push was used

## Versioning

- [ ] `VERSION` bumped
- [ ] `application.software_version` in `config/base.yaml` matches `VERSION`
- [ ] `catalogue_version` in `config/validation_rules.yaml` bumped if rules changed
- [ ] `CONTRACT_VERSION` in `backend/version.py` bumped if contracts changed
- [ ] Documentation updated for anything a tester will notice

---

## Sign-off

```
Release:        _______________
Date:           _______________
Prepared by:    _______________
Audit result:   PASS / FAIL
Tests:          PASS / FAIL
Clean deploy:   PASS / FAIL
Pushed commit:  _______________
```

A release with a FAIL in the Security section is not released. There is no
override path, because the point of the section is that there is no override
path.
