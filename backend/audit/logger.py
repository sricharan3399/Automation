"""Audit logging.

Every significant action records who did what, to which entity, what the value
was before and after, and under which software and rule versions.

Two properties are deliberate:

* **Append-only.** There is no update or delete path here, and the API exposes
  none either. An audit trail that can be edited is not an audit trail.
* **Dual sink.** Records go to the database (queryable from the Audit Logs page)
  and, during a run, to ``audit.jsonl`` inside the run directory, so the run
  output is self-contained and survives independently of the database.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.configstore import get_config_store
from backend.models.orm import AuditEvent
from backend.version import SOFTWARE_VERSION

log = logging.getLogger(__name__)

# --- action vocabulary -----------------------------------------------------
ACTION_RUN_CREATED = "run.created"
ACTION_RUN_STARTED = "run.started"
ACTION_RUN_PAUSED = "run.paused"
ACTION_RUN_RESUMED = "run.resumed"
ACTION_RUN_CANCELLED = "run.cancelled"
ACTION_RUN_COMPLETED = "run.completed"
ACTION_RUN_FAILED = "run.failed"
ACTION_RUN_CHECKPOINT = "run.checkpoint"
ACTION_EVENT_PROCESSED = "event.processed"
ACTION_EVENT_UPSERTED = "event.upserted"
ACTION_EVENT_BLOCKED = "event.blocked"
ACTION_REVIEW_DECISION = "review.decision"
ACTION_REVIEW_OVERRIDE = "review.override"
ACTION_EXPORT_GENERATED = "export.generated"
ACTION_EXPORT_BLOCKED = "export.blocked"
ACTION_CONNECTION_TESTED = "connection.tested"
ACTION_CONNECTION_UPDATED = "connection.updated"
ACTION_SCHEMA_DISCOVERED = "schema.discovered"
ACTION_PROFILE_SAVED = "profile.saved"
ACTION_CONFIG_CHANGED = "config.changed"
ACTION_SUBMISSION_REFUSED = "submission.refused"


class AuditLogger:
    """Writes audit records to the database and, optionally, to a run file."""

    def __init__(self, session: Session, run_id: str | None = None, run_dir: Path | None = None) -> None:
        self.session = session
        self.run_id = run_id
        self.run_dir = run_dir
        self._lock = threading.Lock()
        self._file: Path | None = None
        if run_dir is not None:
            self._file = run_dir / "audit.jsonl"
            self._file.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        action: str,
        *,
        actor: str = "system",
        actor_role: str = "system",
        entity_type: str = "",
        entity_ref: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> AuditEvent:
        rule_version = get_config_store().rule_version_signature()
        entry = AuditEvent(
            actor=actor,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_ref=entity_ref,
            run_id=self.run_id,
            before_json=before,
            after_json=after,
            detail=detail,
            software_version=SOFTWARE_VERSION,
            rule_version=rule_version,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(entry)

        if self._file is not None:
            payload = {
                "timestamp": entry.created_at.isoformat(),
                "actor": actor,
                "actor_role": actor_role,
                "action": action,
                "entity_type": entity_type,
                "entity_ref": entity_ref,
                "run_id": self.run_id,
                "before": before,
                "after": after,
                "detail": detail,
                "software_version": SOFTWARE_VERSION,
                "rule_version": rule_version,
            }
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with self._lock:
                try:
                    with self._file.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                except OSError as exc:  # never let audit IO abort processing
                    log.warning("Could not append to the run audit file: %s", exc)

        return entry


def audit(
    session: Session,
    action: str,
    *,
    actor: str = "system",
    actor_role: str = "system",
    entity_type: str = "",
    entity_ref: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: str | None = None,
    run_id: str | None = None,
) -> AuditEvent:
    """Convenience helper for one-off audit records outside a run."""
    return AuditLogger(session, run_id=run_id).record(
        action,
        actor=actor,
        actor_role=actor_role,
        entity_type=entity_type,
        entity_ref=entity_ref,
        before=before,
        after=after,
        detail=detail,
    )
