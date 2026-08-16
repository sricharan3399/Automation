"""Production readiness gate (spec sections 95 and 96).

Answers one question honestly: *may this installation be trusted to produce
production findings?* Every check is evaluated against the live system - the
database, the configured connections, the filesystem - and never against a
stored flag someone could have set optimistically.

The gate is deliberately hard to pass. A check reports PASS only when the thing
it describes was actually observed to be true; anything unknown is WAITING, and
anything known to be wrong is FAIL. ``production_ready`` is the AND of every
mandatory check, so a single unmet requirement is enough to withhold the label.

Nothing here mutates state. It is safe to call at any time, including while a
run is in progress.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.configstore import get_config_store
from backend.connectors.registry import EVENT_SOURCE_KINDS
from backend.models.orm import ConnectionProfile, Event
from backend.settings import get_settings, is_fixture_dataset

log = logging.getLogger(__name__)

PASS = "PASS"
FAIL = "FAIL"
WAITING = "WAITING"
WARNING = "WARNING"


@dataclass
class Check:
    """One readiness requirement and what was actually observed."""

    key: str
    name: str
    status: str
    detail: str
    # A non-mandatory check is reported but cannot block go-live on its own.
    mandatory: bool = True
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "mandatory": self.mandatory,
            "remediation": self.remediation,
        }


@dataclass
class ReadinessReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def production_ready(self) -> bool:
        return all(c.status == PASS for c in self.checks if c.mandatory)

    @property
    def blocking(self) -> list[Check]:
        return [c for c in self.checks if c.mandatory and c.status != PASS]

    def to_dict(self) -> dict[str, Any]:
        blocking = self.blocking
        return {
            "production_ready": self.production_ready,
            "summary": {
                "passed": sum(1 for c in self.checks if c.status == PASS),
                "failed": sum(1 for c in self.checks if c.status == FAIL),
                "waiting": sum(1 for c in self.checks if c.status == WAITING),
                "warnings": sum(1 for c in self.checks if c.status == WARNING),
                "total": len(self.checks),
            },
            "checks": [c.to_dict() for c in self.checks],
            "next_action": (blocking[0].remediation or blocking[0].detail) if blocking else None,
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _check_operating_mode() -> Check:
    settings = get_settings()
    if settings.is_production_mode:
        return Check(
            "operating_mode",
            "Operating mode",
            PASS,
            "AV_MODE=production. Synthetic datasets are refused.",
        )
    return Check(
        "operating_mode",
        "Operating mode",
        FAIL,
        f"AV_MODE={settings.operating_mode}. Demo mode allows synthetic data to be selected.",
        remediation="Set AV_MODE=production in .env and restart.",
    )


def _check_synthetic_disabled(session: Session) -> Check:
    """The synthetic adapter must not be an enabled event source."""
    enabled = session.scalars(
        select(ConnectionProfile.connection_id).where(
            ConnectionProfile.adapter == "synthetic", ConnectionProfile.enabled.is_(True)
        )
    ).all()
    if enabled:
        return Check(
            "mock_adapters",
            "Mock runtime adapters disabled",
            FAIL,
            f"Synthetic adapter enabled on: {', '.join(enabled)}.",
            remediation="Disable the synthetic connection on the Connections page.",
        )
    return Check(
        "mock_adapters",
        "Mock runtime adapters disabled",
        PASS,
        "No synthetic or mock adapter is enabled.",
    )


def _event_sources(session: Session) -> list[ConnectionProfile]:
    return list(
        session.scalars(
            select(ConnectionProfile).where(ConnectionProfile.kind.in_(sorted(EVENT_SOURCE_KINDS)))
        ).all()
    )


def _is_actually_configured(session: Session, profile: ConnectionProfile) -> tuple[bool, str]:
    """Ask the adapter whether it could run, rather than trusting the flag.

    ``enabled`` is a checkbox. It says an operator intends to use the source,
    not that the source has anywhere to read from - an enabled local-files
    connection with no dataset directory is enabled and useless.
    """
    from backend.connectors.registry import ConnectionManager

    try:
        adapter = ConnectionManager(session).adapter_for(profile.connection_id)
    except Exception as exc:  # noqa: BLE001 - report, never raise out of a health check
        return False, f"adapter unavailable ({type(exc).__name__})"

    missing_configuration: Any = getattr(adapter, "missing_configuration", None)
    missing: list[str] = list(missing_configuration()) if callable(missing_configuration) else []
    if missing:
        return False, ", ".join(missing)

    dataset_dir = getattr(adapter, "dataset_dir", "unset")
    if dataset_dir is None:
        return False, "no dataset directory configured"
    return True, "configured"


def _check_source_configured(session: Session, sources: list[ConnectionProfile]) -> Check:
    enabled = [p for p in sources if p.enabled]
    if not enabled:
        return Check(
            "source_configured",
            "Real data source configured",
            WAITING,
            "No event source is enabled.",
            remediation="Open Connections and configure an approved data source.",
        )

    usable: list[str] = []
    unusable: list[str] = []
    for profile in enabled:
        ok, detail = _is_actually_configured(session, profile)
        (usable if ok else unusable).append(
            profile.connection_id if ok else f"{profile.connection_id} ({detail})"
        )

    if not usable:
        return Check(
            "source_configured",
            "Real data source configured",
            WAITING,
            "Enabled but not usable: " + "; ".join(unusable),
            remediation="Finish configuring the source on the Connections page.",
        )
    detail = f"Usable: {', '.join(usable)}."
    if unusable:
        detail += f" Enabled but not usable: {'; '.join(unusable)}."
    return Check("source_configured", "Real data source configured", PASS, detail)


def _check_source_connected(sources: list[ConnectionProfile]) -> Check:
    enabled = [p for p in sources if p.enabled]
    if not enabled:
        return Check(
            "source_connected",
            "Data source connection successful",
            WAITING,
            "No event source is enabled, so none has been probed.",
            remediation="Configure a source, then press TEST CONNECTION.",
        )
    connected = [p for p in enabled if p.last_status == "CONNECTED" and p.last_success_at is not None]
    if not connected:
        untested = [p for p in enabled if p.last_success_at is None]
        if untested:
            return Check(
                "source_connected",
                "Data source connection successful",
                WAITING,
                f"Never connected successfully: {', '.join(p.connection_id for p in untested)}.",
                remediation="Press TEST CONNECTION on the Connections page.",
            )
        return Check(
            "source_connected",
            "Data source connection successful",
            FAIL,
            "; ".join(f"{p.connection_id}: {p.last_status}" for p in enabled),
            remediation="Resolve the connection error reported on the Connections page.",
        )
    return Check(
        "source_connected",
        "Data source connection successful",
        PASS,
        ", ".join(
            f"{p.connection_id} ({p.last_latency_ms:.0f} ms)"
            if p.last_latency_ms is not None
            else p.connection_id
            for p in connected
        ),
    )


def _check_no_fixture_source(sources: list[ConnectionProfile]) -> Check:
    """The single most important check: are we serving test fixtures?"""
    offenders = []
    for profile in sources:
        if not profile.enabled:
            continue
        dataset_dir = (profile.settings_json or {}).get("dataset_dir")
        if is_fixture_dataset(dataset_dir):
            offenders.append(profile.connection_id)

    # The environment default matters too: an adapter with no explicit
    # directory falls back to it.
    if is_fixture_dataset(get_settings().local_dataset_dir):
        offenders.append("AV_LOCAL_DATASET_DIR")

    if offenders:
        return Check(
            "no_fixture_data",
            "Demo and fixture data removed",
            FAIL,
            f"Pointed at committed test fixtures: {', '.join(sorted(set(offenders)))}.",
            remediation=(
                "Point AV_LOCAL_DATASET_DIR (and the connection's dataset directory) at an "
                "approved exported dataset. Results derived from tests/golden_dataset are "
                "synthetic and must never be reported as production findings."
            ),
        )
    return Check(
        "no_fixture_data",
        "Demo and fixture data removed",
        PASS,
        "No enabled source reads from the test-fixture tree.",
    )


def _check_schema_mapped(sources: list[ConnectionProfile]) -> Check:
    enabled = [p for p in sources if p.enabled]
    if not enabled:
        return Check(
            "schema_mapped",
            "Source schema mapped",
            WAITING,
            "No event source is enabled.",
            remediation="Configure a source, then run DISCOVER SOURCE SCHEMA.",
        )
    unmapped = [p for p in enabled if not (p.field_mapping_json or {}).get("mapping")]
    if unmapped:
        return Check(
            "schema_mapped",
            "Source schema mapped",
            WAITING,
            f"No field mapping stored for: {', '.join(p.connection_id for p in unmapped)}.",
            remediation="Run DISCOVER SOURCE SCHEMA on the Connections page and confirm the mapping.",
        )
    return Check("schema_mapped", "Source schema mapped", PASS, "Every enabled source has a field mapping.")


def _check_database(session: Session) -> Check:
    try:
        events = session.scalar(select(func.count()).select_from(Event))
    except Exception as exc:  # pragma: no cover - only on a broken database
        return Check(
            "database",
            "Database healthy",
            FAIL,
            f"Query failed: {exc}",
            remediation="Check the database file and restart the application.",
        )
    return Check("database", "Database healthy", PASS, f"Reachable. {events} event(s) stored.")


def _check_rules_configured() -> Check:
    store = get_config_store()
    try:
        rules = store.rules()
    except Exception as exc:  # pragma: no cover - malformed config
        return Check(
            "rules",
            "Validation rules configured",
            FAIL,
            f"Rule configuration could not be loaded: {exc}",
            remediation="Fix config/validation_rules.yaml.",
        )
    enabled = [r for r in rules if getattr(r, "enabled", True)]
    if not enabled:
        return Check(
            "rules",
            "Validation rules configured",
            FAIL,
            "No validation rule is enabled.",
            remediation="Enable the rules required by your test profile.",
        )
    return Check("rules", "Validation rules configured", PASS, f"{len(enabled)} rule(s) enabled.")


def _writable(path: Path) -> tuple[bool, str]:
    """Prove writability by writing, rather than trusting a permission bit."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readiness_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, str(path)


def _check_output_writable() -> Check:
    ok, detail = _writable(get_settings().output_dir)
    return Check(
        "csv_path_writable",
        "CSV output path writable",
        PASS if ok else FAIL,
        detail if ok else f"Not writable: {detail}",
        remediation=None if ok else "Grant write access to the output directory, or set AV_EVIDENCE_DIR.",
    )


def _check_runtime_writable() -> Check:
    settings = get_settings()
    problems = []
    for path in (settings.checkpoint_dir, settings.cache_dir):
        ok, detail = _writable(path)
        if not ok:
            problems.append(f"{path}: {detail}")
    if problems:
        return Check(
            "runtime_storage_writable",
            "Runtime storage writable",
            FAIL,
            "; ".join(problems),
            remediation="Grant write access to the data/ directory.",
        )
    return Check("runtime_storage_writable", "Runtime storage writable", PASS, "Checkpoint and cache directories writable.")


# Environment variables that would hold a secret in plain text. Only their
# presence is examined - never their value.
_SECRET_ENV_NAMES = ("AV_DATASCOUT_TOKEN", "AV_DATASCOUT_API_KEY")


def _check_secrets_protected() -> Check:
    """Warn when a credential is sitting in the environment or .env file.

    Never reads or reports a secret value - only whether one is present, and
    where. Environment injection is an approved mechanism, so this is a
    non-mandatory check: it informs, it does not block.
    """
    from backend.settings import ENV_FILE

    in_env_file: list[str] = []
    if ENV_FILE.is_file():
        try:
            for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                if key.strip() in _SECRET_ENV_NAMES and value.strip():
                    in_env_file.append(key.strip())
        except OSError:  # pragma: no cover - unreadable .env
            pass

    if in_env_file:
        return Check(
            "secrets_protected",
            "Secrets protected",
            WARNING,
            f"A credential value is present in .env ({', '.join(sorted(set(in_env_file)))}).",
            mandatory=False,
            remediation=(
                "Move it to the Windows Credential Manager or the approved secret injector. "
                ".env is acceptable only for approved local development."
            ),
        )
    present = [name for name in _SECRET_ENV_NAMES if os.environ.get(name)]
    if present:
        return Check(
            "secrets_protected",
            "Secrets protected",
            PASS,
            f"Credential supplied through the environment ({', '.join(present)}); not stored in the repository.",
            mandatory=False,
        )
    return Check(
        "secrets_protected",
        "Secrets protected",
        PASS,
        "No credential is stored in .env or the repository.",
        mandatory=False,
    )


def _check_production_submission() -> Check:
    settings = get_settings()
    if settings.allow_production_submission:
        return Check(
            "read_only",
            "Source access is read-only",
            WARNING,
            "Production submission is ENABLED.",
            mandatory=False,
            remediation="Disable it unless written approval is in place.",
        )
    return Check(
        "read_only",
        "Source access is read-only",
        PASS,
        f"source_access_mode={settings.source_access_mode}, production submission disabled.",
    )


def build_report(session: Session) -> ReadinessReport:
    """Evaluate every readiness check against the live system."""
    sources = _event_sources(session)
    return ReadinessReport(
        checks=[
            _check_no_fixture_source(sources),
            _check_synthetic_disabled(session),
            _check_operating_mode(),
            _check_source_configured(session, sources),
            _check_source_connected(sources),
            _check_schema_mapped(sources),
            _check_database(session),
            _check_rules_configured(),
            _check_output_writable(),
            _check_runtime_writable(),
            _check_production_submission(),
            _check_secrets_protected(),
        ]
    )
