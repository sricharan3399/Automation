# Dashboard Guide

Reference for every page. For the day-to-day sequence, read
[TESTER_WORKFLOW.md](TESTER_WORKFLOW.md) instead.

The header always shows the software, contract and rule versions, your identity and role,
the operating mode, the source access mode, and whether production submission is enabled.

---

## 1. Home

**Connection status** — a card per connection with status, latency and the first line of
any error, plus a GPU card. **Current configuration** — the loaded profile. **Previous run**
— records scanned, country and scenario matches, candidate issues, review required,
blocking errors, duplicates merged, CSV rows and runtime. **Queues** — how much is waiting
for you.

Quick actions: New scout run, Repeat last run, Open review queue, Download last CSV, View
errors, Connection test. Repeat re-runs the *frozen* configuration of the earlier run, so a
repeat cannot silently pick up a changed profile.

## 2. Connections

One card per connection: configured, enabled, last tested, latency, API and schema
versions, permissions, whether the credential is available, and whether the field mapping
is confirmed or auto-suggested.

* **CONFIGURE** — non-secret settings as JSON. Entering a token, password or API key is
  rejected; supply a `credential_key` naming the credential-store entry instead.
* **TEST** — probes the connection and records the result in the audit trail.
* **SCHEMA** — Discover Schema, then the mapping editor.

## 3. Scout Setup

The main query builder. Every multi-select has search, Select All and Clear, and shows
`Any` when empty.

Each field is tagged `source` or `fallback`. `source` values came from the connected
source; `fallback` values came from the bundled taxonomy because the source did not
describe that field.

The **Matching events** counter updates as you change filters, debounced so every click
does not trigger a source query.

**PREVIEW QUERY** shows the resolved filters, the estimate, the native source query and any
warnings. **RUN SCOUT** starts the run and takes you to Live Processing.

## 4. Scenario Builder

Bus scenario tags and ego-lane relationships. These filter *retrieval*; the scene engine
separately detects tags from the data and adds them to each record, so a record can carry
tags you did not filter on.

## 5. Sensor Configuration

Per-stream Required / Optional / Ignore for eight camera positions and twelve other
streams, plus the list of automatic health checks.

Defaults are deliberately minimal — only the master clock stream is required. A Required
stream the source does not deliver blocks the event.

## 6. Map & Lane Setup

Pick an event to see:

* the self-contained SVG map, with toggles for route, lane centrelines, junctions, signals
  and stop lines, derived markers and feature IDs
* the ranked target junction with its reasons and score
* alternative candidates
* the polygon assessment — unique points, area, validity, self-intersection, whether the
  trajectory crosses it, and any proposed correction
* every derived timestamp with its confidence, or the reason it is unavailable

No tile server is contacted; everything is drawn in the event's local metric frame.

## 7. Validation Rules

The catalogue grouped by category. Each rule shows severity, version, whether it blocks
processing or export, whether it needs review, and its state. Rules awaiting an approved
project threshold are shown as `AWAITING APPROVED PROJECT THRESHOLD` and cannot be enabled.

Click a rule id for its description, inputs, threshold and source. The confidence routing
policy is shown at the bottom.

**RELOAD FROM DISK** re-reads the YAML without a restart.

## 8. Automation Runs

Every run with status, stage, counters, elapsed time and controls: WATCH, PAUSE, RESUME,
CANCEL, REPEAT. Resumable checkpoints are listed separately — click one to resume without
reprocessing.

## 9. Live Processing

Live progress over a WebSocket, with automatic reconnect and exponential backoff so a
backend restart does not produce a reconnect storm.

Shows the stage list with completed / active / pending state, the counters, elapsed and
estimated remaining time, and the event currently being processed. Progress survives a page
reload because a new subscriber immediately receives the current state.

## 10. Event Explorer

A virtualised table — only the visible window is mounted, so thousands of events scroll
smoothly. Filter by search text, status and country code.

Click a row for the detail drawer: Summary, Timeline, Sensors, Map, Objects, Behavior,
Validation, Recommendations, Evidence, Review History and Audit.

The Validation tab separates **Findings** from **Not evaluated**, with the reason each rule
was skipped.

## 11. Review Queue

Tabs with live counts: All, High/Medium/Low confidence, Blocking errors, Safety review,
Data issues, Completed, Rejected.

Opening a record gives the side-by-side comparison per field: original, recommendation,
confidence and band, reviewer value, and ACCEPT / REJECT / EDIT. Ranked alternatives are
one click away.

Hovering a confidence value shows the arithmetic — components, the weights actually used,
and which evidence was missing.

Safety-critical fields are outlined and labelled. Overriding one needs a reason of at least
15 characters and the senior-tester role; the backend enforces both.

**CONFIRM RECORD** is disabled while blocking errors remain.

## 12. Evidence Viewer

Available evidence with purpose, kind, timestamp, redaction state and content hash — click
to enlarge an SVG. **Unavailable** evidence is listed separately with the reason, because a
manifest that silently omitted a capture point would read like evidence that was reviewed
and found unremarkable.

The redaction policy is summarised at the top.

## 13. CSV / Reports

Template selection, per-column selection (mandatory columns cannot be removed), export
readiness, a filterable preview, and export.

Readiness shows passed, warnings, blocking errors, exportable rows and rejected rows. While
blocking errors remain the export button stays disabled and the panel says `CSV NOT READY`.

Run files can be downloaded individually.

## 14. Quality Analytics

Events processed, events per hour, candidate error rate, blocking data-error rate, review
acceptance and override rates, duplicates merged, mean sensor quality and mean confidence.

Plus findings by category, top rules by finding count, review quality per field,
per-run performance, and sensor quality by stream.

Review outcomes are recorded for rule and threshold improvement. **No model is retrained
automatically** — that would require an approved governance pipeline.

## 15. Configuration Profiles

Bundled profiles plus your own. LOAD drops a profile into Scout Setup. Bundled profiles
cannot be overwritten or deleted; save under a new id to keep changes.

A profile stores filters, sensor requirements, rule overrides, thresholds, evidence
settings and the CSV schema. It never stores credentials — only the id of a connection
whose secrets live in the credential store.

## 16. Audit Logs

The append-only trail, filterable by action and run. Each entry shows when, who and in
which role, the action, the entity, the before and after values, and the software and rule
versions in force. There is no edit or delete.

## 17. System Health

Live CPU, RAM, disk, GPU, active and resumable runs, plus the full environment check list
with PASS / WARNING / FAIL and a detail line for each.

## 18. Administration

Requires the administrator role. Shows the operating mode, roles and permissions, the
retention report, versions and the full effective configuration. **RELOAD CONFIGURATION**
re-reads `config/*.yaml`.

The retention view is a **report, not an action**: deletion of AV records is never
automatic.

---

## Conventions

**Colours.** Green passed / connected / confirmed. Blue running / auto-prepared. Amber
warning / review required / paused. Red blocking / failed / error. Grey unknown /
not configured.

**Confidence.** ≥95% green, ≥80% blue, ≥50% amber, below 50% red.

**Errors.** Every error message is actionable text, never a bare status code. Where a retry
makes sense, a RETRY control is offered.

**Density.** Deliberately dense and keyboard-friendly, with no decorative animation — this
is an engineering tool that sits next to video review screens.
