# Troubleshooting

## Startup

**`Dependencies are not installed`**
Run `install_windows.ps1`, or `python -m venv .venv` followed by
`.venv\Scripts\python.exe -m pip install -r requirements.txt`.

**`The dashboard has not been built yet`**
`cd dashboard && npm install && npm run build`, then restart. The API stays fully usable at
`/api/docs`. `install_windows.ps1 -SkipDashboard` skips this deliberately.

**Port already in use**
`AV_PORT=8010` in `.env`, or `launcher.py start --port 8010`.

**`403 Forbidden` on Administration**
The default role is `tester`. Set `AV_LOCAL_ROLE=administrator` and restart. The header
shows your current identity and role.

---

## Connections

**Data Scout shows `NOT_CONFIGURED`**
Expected. No approved endpoint, authentication mode or schema has been supplied, so the
adapter refuses to operate rather than fabricating a connection. The status message lists
exactly what is missing. See [DATA_SCOUT_INTEGRATION.md](DATA_SCOUT_INTEGRATION.md).

**`Secret values are never stored in a connection profile`**
Correct behaviour. Supply `credential_key` naming the OS credential-store entry or the
injected environment variable, not the secret itself.

**`No credential found for 'AV_DATASCOUT_TOKEN'`**
Store it:

```powershell
.\.venv\Scripts\python.exe -c "import keyring; keyring.set_password('av-test-automation','AV_DATASCOUT_TOKEN','THE-TOKEN')"
```

or inject it through the approved secret manager.

**Local adapter finds no events**
Check `AV_LOCAL_DATASET_DIR`. The adapter expects `<dir>/events/*.json` (authoritative when
present), `<dir>/*.json`, or `<dir>/*.csv`. Regenerate the fixtures with
`python tests/golden_dataset/generate.py`.

**`Synthetic data is refused while the platform is in production mode`**
Correct behaviour: production mode never substitutes fake data for an unavailable source.
Set `AV_MODE=demo` deliberately, or connect an approved source.

---

## Queries

**Estimated records is 0**
The filters are too narrow. The dashboard suggests what to relax: remove the city
restriction, widen the date range, set road type to Any, set lane count to Any. No run ever
fabricates rows to fill a result set.

**Dropdown values are tagged `fallback`**
The source did not describe its own vocabulary for that field, so the bundled taxonomy is
shown. Useful, but not evidence that those values exist in the data. Configure a
`supported_filters` endpoint to get source-derived values.

**`The source cannot express these filters natively`**
Those filters are applied locally after retrieval — correct, but slower. Add the missing
entries to `query_translation` on the connection to push them into the source query.

**Events come back with the wrong country**
Check `DATA_COUNTRY_AUTHORITATIVE` on the Event Detail → Validation tab. Country resolved
from a filename-like field is rejected. Map an authoritative `country_code` field in the
mapping editor.

---

## Runs

**Run stuck at `PENDING`**
Check the console log. A run thread that fails sets `FAILED` with the reason. If the
process was killed, resume the checkpoint from Automation Runs.

**`Data Scout is temporarily unavailable`**
The run checkpointed. Use RESUME on Automation Runs; processing continues from where it
stopped rather than starting over.

**`N consecutive source errors`**
The source is failing repeatedly. Progress was checkpointed. Test the connection, then
resume.

**Everything ends up `BLOCKED_DATA_ERROR`**
A stream marked *Required* on the Sensor Configuration page is missing from the export.
Either the export is incomplete, or that stream should not be Required for this dataset.
The finding names the missing stream.

**A dry run produced no events in the Event Explorer**
Correct: a dry run writes nothing. Turn DRY RUN off for a real run.

---

## Review

**Every record needs review**
Expected on a first run. Confidence depends on how much corroborating evidence the source
provides — a single camera with no map context cannot produce high confidence, and the
platform will not pretend otherwise. Click a confidence value to see which evidence was
missing.

**`An override reason of at least 15 characters is required`**
Deliberate. High-severity and safety-critical overrides must record why the automated
recommendation was not accepted.

**`Overriding it requires the senior tester role`**
Safety-critical fields need `AV_LOCAL_ROLE=senior_tester` or `administrator`.

**`This record still has N blocking error(s)`**
A record cannot be confirmed while blocking errors remain. Open the Validation tab, resolve
each, then confirm.

---

## Export

**`CSV NOT READY`**
The readiness panel lists each blocking issue with its rule and a recommended correction.
Common causes: a mandatory column blank because its confidence was too low to auto-select,
a value outside the taxonomy, or two records resolving to the same canonical key.

**Where did the rejected rows go?**
`rejected_records.csv` in the run directory, each with the rule that rejected it, the
reason and a recommended correction. Nothing is silently dropped.

**`results_partial.csv` instead of `results.csv`**
Some rows were blocked. The clean rows were still written, under a name that makes the
partial state obvious.

**A cell is blank that I expected to be filled**
Its confidence was below the auto-selection threshold, so it was deliberately left blank
rather than guessed. Fill it in the Review Queue. This is what makes an exported value
mean *a human accepted this, or the machine was confident enough* — never *the machine's
best guess, unlabelled*.

---

## Evidence

**Camera-frame evidence is unavailable**
Expected. Stream manifests reference frames but do not carry pixels, and no approved
frame provider is configured. The item is recorded as unavailable **with that reason**
rather than omitted, because a manifest missing a capture point reads like evidence that
was reviewed and found unremarkable.

Map/trajectory diagrams, telemetry summaries, validation warnings and the final review
snapshot are always produced.

**`Redaction is required but could not be applied`**
Fail-closed by design. Image redaction needs an image library: `pip install -e ".[vision]"`,
or export from inside the approved environment.

---

## Development

**Tests hang**
`pytest` is configured with a 300-second per-test timeout, so a hang fails rather than
occupying the machine. If a test times out, the traceback shows where.

**The golden dataset determinism test fails**
Either the generator became non-deterministic, or the fixtures are stale. Run
`python tests/golden_dataset/generate.py` and re-test. The failure names the differing
fixtures rather than diffing megabytes of JSON.

Beware ordering that depends on `set` iteration or `dict` insertion of hashed strings —
Python randomises string hashing per process, so a tie broken by `max(set(...))` gives a
different answer on every run. That exact bug is what this test was written to catch.

**Dashboard import fails with `@/…` unresolved**
The alias must be declared in both `tsconfig.json` (`paths`) and `vite.config.ts`
(`resolve.alias`). Both are configured; if you add another alias, add it to both.
