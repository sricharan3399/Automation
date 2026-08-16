"""REST and WebSocket API routers."""

from backend.api import (
    admin,
    analytics,
    audit,
    connections,
    events,
    evidence,
    home,
    profiles,
    reports,
    review,
    rules,
    runs,
    system,
    taxonomy,
    ws,
)

ROUTERS = [
    system.router,
    home.router,
    connections.router,
    taxonomy.router,
    profiles.router,
    runs.router,
    events.router,
    review.router,
    rules.router,
    reports.router,
    evidence.router,
    analytics.router,
    audit.router,
    admin.router,
]

__all__ = ["ROUTERS", "ws"]
