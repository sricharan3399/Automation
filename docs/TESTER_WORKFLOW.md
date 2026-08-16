# Tester Workflow

The day-to-day loop, end to end. You should not need to touch YAML, Python, a terminal or
a CSV editor at any point.

```
Open dashboard → check connections → load or build a query → preview → dry run
      → full run → watch live → review → export → hand over
```

---

## 1. Open the dashboard

```powershell
.\.venv\Scripts\python.exe launcher.py
```

Or use the **Start AV Scout Dashboard** desktop shortcut. The browser opens at
<http://localhost:8000>.

Opening the application does **not** start a query. Nothing touches a data source until
you press RUN.

## 2. Check the connections

**Home** shows a card per connection. You need the event source to read `CONNECTED`.

If Data Scout reads `NOT_CONFIGURED`, that is expected until the approved connection
details are supplied — see [DATA_SCOUT_INTEGRATION.md](DATA_SCOUT_INTEGRATION.md). You can
still work against an approved exported dataset via the Local CSV / JSON connection.

Press **CONNECTION TEST** to re-test everything.

## 3. Load a profile, or build a query

**Configuration Profiles** ships with:

* Germany Bus Validation (the default)
* Germany Bus Urban / Autobahn
* Germany Traffic Light
* Germany Complex Junction
* Night Bus Scenarios
* Rain Bus Scenarios

Press **LOAD** to drop one into Scout Setup, then adjust.

To build from scratch, go to **Scout Setup**:

| Control | Notes |
|---|---|
| Country | Resolved against authoritative `country_code` metadata, never a filename |
| Region / City / Test area | Populated from the source; narrows as you pick a country |
| Object type / Bus subtype | |
| Road type | |
| Lane count | `Any`, exact values, or a min/max range. `5+` means five or more |
| Lane configuration / Relation to ego | |
| Intersection type / Complexity | |
| Traffic control / Signal state | |
| Manoeuvre / Weather / Lighting | |
| Date & time | Plus day-only, night-only, weekday, weekend |
| Dataset | Project, dataset, version, build, software and map version |

**An empty multi-select means "Any".** The UI states this next to every field.

Values tagged `source` came from the connected source. Values tagged `fallback` came from
the bundled taxonomy because the source did not describe that field — useful, but not
evidence that those values exist in the data.

The **Matching events** counter updates as you change filters.

## 4. Set sensor requirements

**Sensor Configuration**: mark each stream Required, Optional or Ignore.

A stream marked *Required* that the source does not deliver **blocks that event** and
routes it to data review. The defaults are deliberately minimal — only the master clock
stream is required — so you opt in to strictness rather than fighting it.

## 5. Preview the query

**PREVIEW QUERY** shows the resolved filters, the estimated record count, the native query
sent to the source, and any warnings. Common warnings:

* *No filters are set* — the query matches everything the source can return.
* *The source cannot express these filters natively* — they are applied locally after
  retrieval, which is slower but correct.
* *DEMO MODE* — results may be synthetic.

## 6. Dry run first

Leave **DRY RUN** on for a new configuration. A dry run:

* tests the connection
* validates the configuration
* estimates the record count
* processes a small sample **in memory**
* writes nothing, exports nothing, produces no evidence

The first execution of a never-run profile is forced to dry-run even if you turn the
toggle off.

## 7. Run

Turn off DRY RUN and press **RUN SCOUT**. You land on **Live Processing**, which streams:

* stage-by-stage pipeline progress
* records discovered / processed / filtered
* candidate issues, blocking errors, review-required counts
* elapsed and estimated remaining time
* the event currently being processed

**PAUSE**, **RESUME** and **CANCEL** are available throughout. A paused or cancelled run
saves a checkpoint; **Automation Runs** lists resumable checkpoints and resumes without
reprocessing.

If the source becomes unavailable mid-run you get a message like:

> Data Scout is temporarily unavailable. Events processed before the interruption: 842.
> Progress was checkpointed.

## 8. Review

**Review Queue**, with tabs for All, High/Medium/Low confidence, Blocking errors, Safety
review, Data issues, Completed and Rejected.

Open a record for the side-by-side view:

| Field | Original | Recommended | Confidence | Reviewer value |
|---|---|---|---|---|

For each field: **ACCEPT**, **REJECT**, or **EDIT**. Ranked alternatives are one click away.

Click any confidence value to see how it was produced — the components, the weights
actually used, and which evidence was missing. Missing evidence never counts as agreement.

Two rules the UI enforces:

* An override on a high-severity or safety-critical field needs a reason of at least 15
  characters. There is no way to record one without it.
* Overriding a safety-critical field needs the senior-tester role.

Then **CONFIRM RECORD** or **REJECT RECORD**. Confirming is blocked while the record still
has blocking errors.

Every decision is written to the append-only audit trail with your identity, the original
recommendation, your value, the reason, and the model and rule versions in force.

## 9. Export

**CSV / Reports**:

1. Pick a template — Germany Bus Test, Generic AV Event, Perception Validation,
   Traffic-Light Validation, Map/Geometry QA, or Sensor Quality.
2. Choose columns. Mandatory columns cannot be removed.
3. **PREVIEW** to see rendered rows and export readiness:

```
Passed: 47   Warnings: 3   Blocking errors: 1
CSV NOT READY
```

4. Fix blocking issues in the Review Queue, re-preview, then **EXPORT CSV**.

Blocked rows are never silently dropped. They go to `rejected_records.csv` with the rule
that rejected them and a recommended correction.

## 10. Hand over

Each run produces a self-contained directory under `output/run_<timestamp>/` containing
the CSV, rejected records, summary, validation report, frozen run configuration, evidence
manifest, the audit trail and the evidence itself. Download individual files from the
Reports page.

---

## What the statuses mean

| Status | Meaning |
|---|---|
| `CANDIDATE` | A machine finding, not yet routed |
| `AUTO_PREPARED` | Every field prefilled, no rule failed — still needs confirmation |
| `REVIEW_REQUIRED` | Needs your decision |
| `SENIOR_REVIEW_REQUIRED` | Safety-critical disagreement; needs a senior tester |
| `BLOCKED_DATA_ERROR` | The data was unusable — a data problem, **not** a vehicle finding |
| `CONFIRMED_BY_TESTER` | You confirmed it |
| `REJECTED_BY_TESTER` | You rejected it |

No status in this list means "confirmed defect". That classification requires a validated
project rule, and the platform will not apply one it has not been given.

---

## Reading the validation report

Three outcomes, kept strictly separate:

* **passed** — the rule ran and the condition held.
* **failed** — the rule ran and the condition did not hold.
* **skipped** — the rule could not run, with a stated reason.

A skipped rule is not a passing rule. If perception rules were skipped because no
reference annotations exist, that is *not* evidence that perception was correct, and the
report says so.

---

## When no events match

The dashboard suggests what to relax rather than returning an empty CSV:

* remove the city restriction
* widen the date range
* set road type to Any
* set lane count to Any

No run ever fabricates rows to fill a result set.
