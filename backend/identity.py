"""Pseudonymisation and canonical event identity.

Two related jobs:

* **Pseudonymisation** - internal session, event, job and vehicle identifiers
  are replaced by stable, salted, non-reversible references before anything
  leaves the approved environment. The same input always yields the same
  pseudonym within an installation (so records can be correlated across runs),
  and never the same pseudonym across installations.

* **Canonical event key** - a deterministic SHA-256 over the identifying
  characteristics of an event. Re-processing the same event produces the same
  key, which is what makes the pipeline idempotent: a matching record is
  UPSERTED, never inserted twice.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from backend.settings import PROJECT_ROOT, get_settings

SALT_ENV_VAR = "AV_REDACTION_SALT"
SALT_FILE = PROJECT_ROOT / "data" / ".installation_salt"

#: Event time is rounded to this many seconds when forming the canonical key,
#: so a sub-second timestamp difference between two exports of the same event
#: does not create a duplicate record.
EVENT_TIME_ROUNDING_S = 1.0

_cached_salt: str | None = None


def get_salt() -> str:
    """Resolve the installation salt.

    Order: environment (approved secret injector) -> per-installation file
    generated on first use. The file lives outside the repository tree that is
    published, and is gitignored.
    """
    global _cached_salt
    if _cached_salt is not None:
        return _cached_salt

    env_value = os.environ.get(SALT_ENV_VAR)
    if env_value:
        _cached_salt = env_value
        return _cached_salt

    if SALT_FILE.is_file():
        stored = SALT_FILE.read_text(encoding="utf-8").strip()
        if stored:
            _cached_salt = stored
            return _cached_salt

    generated = secrets.token_hex(32)
    try:
        SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        SALT_FILE.write_text(generated, encoding="utf-8")
    except OSError:
        # An unwritable data directory must not stop processing; the salt then
        # lives only for this process, which is still non-reversible.
        pass
    _cached_salt = generated
    return _cached_salt


def reset_salt_cache() -> None:
    """Used by tests."""
    global _cached_salt
    _cached_salt = None


def pseudonymize(value: str | None, prefix: str = "REF", length: int = 12) -> str:
    """Stable, salted, non-reversible reference for an internal identifier."""
    if not value:
        return f"{prefix}-UNKNOWN"
    digest = hashlib.sha256(f"{get_salt()}::{prefix}::{value}".encode()).hexdigest()
    return f"{prefix}-{digest[:length].upper()}"


def anonymized_session_ref(session_id: str | None) -> str:
    return pseudonymize(session_id, prefix="SES")


def anonymized_event_ref(event_id: str | None) -> str:
    return pseudonymize(event_id, prefix="EVT")


def anonymized_job_ref(job_ref: str | None) -> str | None:
    return pseudonymize(job_ref, prefix="JOB") if job_ref else None


def _round_time(value: datetime | None) -> str:
    if value is None:
        return "no-event-time"
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    epoch = moment.timestamp()
    rounded = round(epoch / EVENT_TIME_ROUNDING_S) * EVENT_TIME_ROUNDING_S
    return f"{rounded:.0f}"


def canonical_event_key(
    session_ref: str,
    event_type: str,
    event_time: datetime | None,
    target_junction_ref: str | None = None,
) -> str:
    """Deterministic identity for one event occurrence.

    Inputs are the *anonymised* session reference, the canonical event type,
    the rounded event time and the target junction. Two exports of the same
    event agree on all four, so they collapse onto one record.
    """
    parts = [
        session_ref or "no-session",
        (event_type or "unknown").strip().lower(),
        _round_time(event_time),
        (target_junction_ref or "no-junction").strip(),
    ]
    payload = "|".join(parts)
    return hashlib.sha256(f"{get_salt()}::event::{payload}".encode()).hexdigest()


def content_hash(data: bytes) -> str:
    """Evidence integrity hash (unsalted - it must be independently verifiable)."""
    algorithm = str(get_settings().section("evidence").get("hash_algorithm", "sha256"))
    digest = hashlib.new(algorithm if algorithm in hashlib.algorithms_available else "sha256")
    digest.update(data)
    return f"{digest.name}:{digest.hexdigest()}"


def file_hash(path: Path) -> str | None:
    try:
        return content_hash(path.read_bytes())
    except OSError:
        return None
