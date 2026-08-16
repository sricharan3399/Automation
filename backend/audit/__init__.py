"""Append-only audit trail."""

from backend.audit.logger import AuditLogger, audit

__all__ = ["AuditLogger", "audit"]
