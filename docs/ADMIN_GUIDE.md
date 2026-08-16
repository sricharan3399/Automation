# Administrator Guide

Requires `AV_LOCAL_ROLE=administrator` (or the equivalent forwarded role behind an approved
proxy).

---

## What you configure

| Area | Where | Applied by |
|---|---|---|
| Filters, sensors, rules, CSV schema per run | Configuration Profiles | Tester |
| Data sources, field mappings | Connections | Administrator |
| Rule enablement and thresholds | `config/validation_rules.yaml` | Reload from disk |
| Confidence bands and safety-critical fields | `config/confidence_thresholds.yaml` | Reload |
| Redaction policy | `config/redaction_regions.yaml` | Reload |
| Taxonomy fallback | `config/taxonomy.yaml` | Reload |
| CSV templates | `config/csv_templates/*.yaml` | Reload |
| Platform behaviour | `config/base.yaml` + environment | Reload / restart |

**Administration → RELOAD CONFIGURATION** re-reads the YAML without a restart and writes an
audit record with the before and after versions.

---

## Countries

`config/base.yaml`:

```yaml
countries:
  default: "DE"
  allowed:
    - { code: "DE", name: "Germany" }
    - { code: "FR", name: "France" }
```

This drives the Country dropdown and the `CSV_COUNTRY_CONSISTENCY` check. When a source
exposes its own country list, the source list wins and the dashboard labels it `source`.

---

## Data sources

See [DATA_SCOUT_INTEGRATION.md](DATA_SCOUT_INTEGRATION.md) for the full connection
configuration.

**Never enter a secret on the Connections page.** The API rejects any settings body
containing a secret-shaped key. Supply `credential_key` naming the OS credential-store
entry or the injected environment variable.

To store a credential on Windows:

```powershell
.\.venv\Scripts\python.exe -c "import keyring; keyring.set_password('av-test-automation', 'AV_DATASCOUT_TOKEN', 'THE-TOKEN')"
```

`keyring` is optional; without it the platform falls back to the environment and the
environment check reports that clearly.

---

## Rules and thresholds

Edit `config/validation_rules.yaml`, then Reload. Each rule carries a `version` recorded on
every result, so bump it when you change behaviour — otherwise two runs with different
semantics will claim the same rule version.

Rules with `threshold_source: project` ship disabled and show as
`AWAITING APPROVED PROJECT THRESHOLD`. To activate one, supply the **approved** value and
set `enabled: true`. The platform will not invent a safety-relevant threshold, and neither
should a configuration change that has not been approved.

`config/base.yaml` holds the platform's own data-quality thresholds — synchronisation
budgets, availability floors, geometry plausibility, behaviour thresholds. These are
usability limits, not project safety criteria.

---

## Confidence policy

`config/confidence_thresholds.yaml`:

* **Bands** — the four routing bands and what each does.
* **Fields** — which per-field confidences exist and which are `safety_critical: true`.
  Any disagreement on a safety-critical field routes the record to senior review regardless
  of confidence.
* **Components** — the evidence components and their weights. Weights are renormalised over
  the components actually available, so missing evidence never counts as agreement.
* **hard_floor** — no band may auto-select below this, whatever its own configuration says.

Widening a band or lowering the floor increases how much is auto-selected without human
confirmation. Treat both as approval-worthy changes.

---

## CSV templates

Add a file to `config/csv_templates/`:

```yaml
id: my_template
name: "My Template"
version: "1.0.0"
columns:
  - { key: canonical_event_key, header: canonical_event_key, type: string, required: true }
  - { key: road_type, header: road_type, type: string, required: false, enum: road_type }
```

`key` must exist in the flat export record built by
`backend/reports/export_record.py`. `enum` names a `config/taxonomy.yaml` list validated by
`CSV_ENUM_VALIDITY`. `required: true` makes a blank value a blocking export error.

---

## Roles

| Role | May |
|---|---|
| `viewer` | View |
| `tester` | Run scouts, review, export, save profiles |
| `senior_tester` | + approve safety-critical overrides |
| `administrator` | + manage connections, rules and administration |

`submit_production` is granted to **no role**. Production submission is disabled
platform-wide; granting the permission alone would not enable it.

---

## Retention

Administration → Retention reports how many runs and events fall outside
`storage.retention_days`. It is a **report, not an action** — deletion of AV records is
never automatic and is performed deliberately under the approved data-retention process.

---

## Monitoring a deployment

**System Health** shows CPU, RAM, disk, GPU, active and resumable runs, plus the
environment checks.

**Quality Analytics** is the operational signal: a rising blocking data-error rate points
at the source or the export, not the vehicle; a rising reviewer override rate points at a
rule or threshold that needs revisiting.

Review outcomes are recorded for that purpose. **No model is retrained automatically** —
that requires an approved governance pipeline.

---

## Audit

Append-only, with no update or delete path. Every connection test, configuration change,
schema discovery, run lifecycle event, reviewer decision, override and export attempt is
recorded with the actor, the values before and after, and the software and rule versions in
force.

Audit records are written to the database *and* to `audit.jsonl` inside each run directory,
so a run's output can be handed over on its own.

---

## Upgrades

`python -m backend.cli init-db` is idempotent. It creates missing tables and seeds built-in
profiles that do not exist yet; it never overwrites an existing row, so a tester's edits to
a built-in profile survive.

Review `catalogue_version` in `config/validation_rules.yaml` after an upgrade: a changed
rule catalogue means results from before and after are not directly comparable, which is
exactly why every run records the rule version it used.
