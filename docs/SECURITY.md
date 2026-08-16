# Security

## Threat model

The platform runs on an approved workstation inside the corporate network and reads AV
data that must not leave the approved environment. The risks it is built against are:

1. A credential leaking into the repository, the database, a profile or an API response.
2. Raw AV data (frames, GPS, HD map, telemetry) leaving the approved environment.
3. An automated action mutating source data.
4. An unreviewed machine finding being treated as a confirmed defect.
5. CI reaching production data.

---

## Secrets

**Resolution order** (`backend/auth/secrets.py`):

1. OS credential store — Windows Credential Manager, macOS Keychain, Secret Service — via
   the optional `keyring` package.
2. Environment variable, populated by the approved secret injector or, for approved local
   development only, a gitignored `.env`.

There is no third step. A missing secret raises `SecretNotAvailable` with instructions.
There is deliberately no "return a placeholder" path, because a placeholder would let a
caller believe it is authenticated.

**Secrets are never:**

* committed — `.gitignore` covers `.env`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.crt`,
  `credentials.json`, `secrets.yaml`
* stored in the database — connection profiles hold a `credential_key` *naming* the entry,
  never the value
* stored in a configuration profile — the API rejects any profile whose body contains a
  secret-like key, with an explanatory error
* returned by the API — `/connections` masks anything secret-shaped as `***withheld***`
* logged — no code path formats a resolved secret into a log record

The dashboard states this at the point of entry, and the backend enforces it rather than
trusting the UI. Both paths are covered by tests.

---

## Data that must stay inside the approved environment

Never sent anywhere outside the approved environment:

* camera frames and video
* GPS / precise position
* HD map data
* telemetry and CAN
* reference annotations
* internal event, session, job and vehicle identifiers
* vehicle build and configuration detail

Structural measures rather than policy alone:

* **Local metric frame.** Geometry is stored and rendered as east/north metres relative to
  the event origin. Exported geometry therefore carries no global position at all.
* **Offline map rendering.** The map view is a self-contained SVG. No tile server is
  contacted, so there is no request that *could* carry a coordinate. The dashboard's
  Content-Security-Policy restricts `connect-src` to same-origin plus WebSocket.
* **No external calls.** The dashboard bundle has no runtime dependency on any external
  host. Fonts, styles and scripts are all local.

---

## Pseudonymisation

Internal identifiers are replaced by salted, non-reversible references before anything
leaves the environment:

| Source | Becomes |
|---|---|
| `session_id` | `SES-<12 hex>` |
| `event_id` | `EVT-<12 hex>` |
| `job_ref` | `JOB-<12 hex>` |

Stable within an installation, so records correlate across runs; different across
installations, so references cannot be matched between them.

The salt comes from `AV_REDACTION_SALT`, or a per-installation salt generated on first use
and written to `data/.installation_salt` (gitignored). It is never committed.

---

## Redaction

Configured in `config/redaction_regions.yaml`. **Fail-closed**: if redaction is required
and cannot be applied, the export is refused rather than shipped unredacted.

Three layers:

1. **Structural** — identifiers pseudonymised, coordinates reduced to the configured
   precision (2 decimals ≈ 1 km).
2. **Pattern** — every exported text value scanned for e-mail addresses, IPv4 addresses,
   bearer tokens, API-key-shaped assignments, Windows user paths, UNC shares, German
   plates and VINs. Hits are masked and reported.
3. **Image** — configured regions burned out of exported raster evidence: browser URL bar,
   user identity chip, internal-ID sidebar, burned-in GPS overlays.

**Evidence Viewer → redaction preview** shows exactly what redaction would do to a payload
without writing anything.

Image redaction needs an image library. Where none is available and redaction is required,
the export is refused with that reason — it is not skipped.

---

## Read-only source access

`backend/connectors/base.py` defines the only interface to any source. It exposes
`authenticate`, `test_connection`, discovery and retrieval methods — and **no** create,
update, delete or annotate method of any kind. There is no code path through which the
platform can mutate source data, because no such method exists to call.

`AV_SOURCE_ACCESS_MODE` defaults to `read_only` and is reported on Home, in `/health` and
in every run's frozen configuration.

---

## Production submission

Disabled, and not implemented as a one-click action. `POST /admin/production-submission`
returns `501` with the approved flow stated, and writes an audit record that submission was
requested and refused.

The `submit_production` permission is granted to **no role** by default.

If it is ever enabled, the approved flow is: review → validation gate → submission preview
→ explicit reviewer confirmation → submit → read-back verification → audit.

---

## Browser automation

Not implemented, and disabled by configuration (`AV_ALLOW_BROWSER_AUTOMATION=false`).

If policy later permits it, the approved approach is Playwright driven by `data-testid`,
ARIA labels and semantic selectors — never screen-coordinate clicking — read-only by
default, and never clicking a final submit control.

---

## Role-based access

| Role | Permissions |
|---|---|
| Viewer | view |
| Tester | view, run scout, review, export CSV, save profile |
| Senior tester | + approve safety-critical overrides |
| Administrator | + manage connections, rules and administration |

Enforced server-side. The role is resolved from `AV_LOCAL_ROLE` for a local desktop
deployment, or from an approved authenticating reverse proxy's forwarded headers.

The default role is `tester`, so the Administration pages return `403` until an
administrator role is configured. That is intentional: the secure default is the one that
grants least.

---

## Audit trail

Append-only. Every significant action records who, what, which entity, the value before
and after, and the software and rule versions in force.

There is **no** update or delete endpoint for audit records — an audit trail that can be
edited is not an audit trail. Reviewer decisions are superseded, never overwritten, so the
full decision history stays queryable.

Records go to the database *and* to `audit.jsonl` inside the run directory, so a run's
output is self-contained.

---

## Repository hygiene

The repository holds source, tests, configuration templates, migrations, documentation and
CI. It must never hold raw AV media, dataset exports, production CSVs, credentials or
proprietary maps.

`.gitignore` blocks `*.mp4`, `*.avi`, `*.mkv`, `*.h264`, `*.pcap`, `*.bag`, `*.mcap`,
`*.pcd`, `*.las`, `*.laz`, plus `data/`, `output/` and every credential pattern.

CI fails the build if a credential-like or AV-media file is ever tracked.

The only committed data is the **synthetic golden dataset**, generated deterministically
from source, stamped `is_synthetic: true`, and naming the defects deliberately injected
into each fixture.

---

## CI

CI runs on GitHub-hosted runners, which **must never be able to reach production AV data**.
Every job runs offline against the synthetic dataset, with `AV_MODE=production`,
`AV_DATASCOUT_ENABLED=false` and `AV_ALLOW_PRODUCTION_SUBMISSION=false` forced in the
workflow environment.

Connecting CI to an internal source would require an approved self-hosted runner and is
deliberately not configured.

---

## Reporting a problem

Report suspected exposure of AV data or credentials through the internal security incident
process. Do not open a public issue and do not attach affected data to a ticket.
