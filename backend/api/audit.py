"""Audit log query API.

Read-only by design: there is no endpoint that updates or deletes an audit
record, because an audit trail that can be edited is not an audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from backend.api.deps import DbSession
from backend.models.orm import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit(
    session: DbSession,
    action: str | None = None,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_ref: str | None = None,
    run_id: str | None = None,
    since: datetime | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    statement = select(AuditEvent)
    if action:
        statement = statement.where(AuditEvent.action == action)
    if actor:
        statement = statement.where(AuditEvent.actor == actor)
    if entity_type:
        statement = statement.where(AuditEvent.entity_type == entity_type)
    if entity_ref:
        statement = statement.where(AuditEvent.entity_ref == entity_ref)
    if run_id:
        statement = statement.where(AuditEvent.run_id == run_id)
    if since:
        statement = statement.where(AuditEvent.created_at >= since)

    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(
        statement.order_by(desc(AuditEvent.created_at)).offset(offset).limit(limit)
    ).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "audit_pk": a.audit_pk,
                "created_at": a.created_at,
                "actor": a.actor,
                "actor_role": a.actor_role,
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_ref": a.entity_ref,
                "run_id": a.run_id,
                "before": a.before_json,
                "after": a.after_json,
                "detail": a.detail,
                "software_version": a.software_version,
                "rule_version": a.rule_version,
            }
            for a in rows
        ],
        "note": "Audit records are append-only. This API exposes no update or delete operation.",
    }


@router.get("/actions")
def actions(session: DbSession) -> dict[str, Any]:
    rows = session.execute(
        select(AuditEvent.action, func.count(AuditEvent.audit_pk)).group_by(AuditEvent.action)
    ).all()
    return {"actions": {action: int(count) for action, count in sorted(rows)}}
