"""FastAPI application factory.

Serves the REST API under ``/api/v1``, the WebSocket progress feed, and the
built React dashboard as static files when it has been built. Startup performs
first-run initialisation (schema, built-in profiles, directories) but never
starts a data-source query on its own - opening the application must not touch
production data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.api import ROUTERS, ws
from backend.configstore import get_config_store
from backend.connectors.base import AdapterError
from backend.database.init_db import initialize_database
from backend.database.session import session_scope
from backend.evidence.redaction import RedactionError
from backend.settings import PROJECT_ROOT, get_settings, network_exposure_warning
from backend.version import API_PREFIX, CONTRACT_VERSION, SOFTWARE_VERSION
from backend.workers.progress import get_progress_hub

log = logging.getLogger(__name__)

DASHBOARD_DIST = PROJECT_ROOT / "dashboard" / "dist"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Startup checks (spec section 90): version, database, config, directories.
    settings.ensure_directories()
    get_config_store().load()
    result = initialize_database()
    get_progress_hub().bind_loop(asyncio.get_running_loop())

    log.info(
        "%s %s ready - mode=%s, source access=%s, production submission=%s, %d tables",
        settings.app_name,
        SOFTWARE_VERSION,
        settings.operating_mode,
        settings.source_access_mode,
        "ENABLED" if settings.allow_production_submission else "DISABLED",
        len(result["tables"]),
    )
    log.info(
        "No data-source query is started automatically. Open the dashboard and configure a run."
    )
    yield
    log.info("Shutting down.")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=SOFTWARE_VERSION,
        description=(
            "Human-in-the-loop autonomous-vehicle test automation platform. "
            "All source integrations are read-only; production submission is disabled."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    if settings.dev_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.dev_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    app.include_router(ws.router)

    # --- error translation: never surface a bare status code -------------
    @app.exception_handler(AdapterError)
    async def adapter_error_handler(_: Request, exc: AdapterError) -> JSONResponse:
        return JSONResponse(
            status_code=503 if exc.retryable else 409,
            content={"detail": {"message": exc.user_message, "retryable": exc.retryable}},
        )

    @app.exception_handler(RedactionError)
    async def redaction_error_handler(_: Request, exc: RedactionError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "message": str(exc),
                    "hint": "Redaction is fail-closed; the export was refused rather than shipped unredacted.",
                }
            },
        )

    @app.get("/api", include_in_schema=False)
    def api_root() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "software_version": SOFTWARE_VERSION,
            "contract_version": CONTRACT_VERSION,
            "api_prefix": API_PREFIX,
            "docs": "/api/docs",
            "websocket": "/ws/runs/{run_id}",
        }

    # Registered BEFORE the dashboard's catch-all route, so a deployment health
    # check gets JSON rather than the SPA shell. Without this, /health would
    # return 200 with an HTML body even when the API was broken, and a startup
    # script polling it would report success for a dead backend.
    @app.get("/health", include_in_schema=False)
    def deployment_health() -> JSONResponse:
        """Liveness probe for deployment scripts and container orchestrators.

        Deliberately minimal and credential-free: version and component status
        only. The detailed report stays behind the versioned API.
        """
        database_ok = True
        try:
            with session_scope() as session:
                session.execute(text("SELECT 1"))
        except Exception:  # pragma: no cover - only on a broken database
            log.exception("Health check could not reach the database")
            database_ok = False

        dashboard_built = (DASHBOARD_DIST / "index.html").is_file()
        payload = {
            "status": "healthy" if database_ok else "degraded",
            "backend": "healthy",
            "database": "healthy" if database_ok else "unavailable",
            "dashboard": "built" if dashboard_built else "not_built",
            "version": SOFTWARE_VERSION,
        }
        return JSONResponse(status_code=200 if database_ok else 503, content=payload)

    _mount_dashboard(app)
    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Serve the built dashboard, or an actionable placeholder when absent."""
    index = DASHBOARD_DIST / "index.html"
    if index.is_file():
        assets = DASHBOARD_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            candidate = (DASHBOARD_DIST / full_path).resolve()
            if (
                full_path
                and str(candidate).startswith(str(DASHBOARD_DIST.resolve()))
                and candidate.is_file()
            ):
                return FileResponse(candidate)
            # Client-side routing: unknown paths fall through to the SPA shell.
            return FileResponse(index)

        return

    @app.get("/", include_in_schema=False)
    async def not_built() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "message": "The dashboard has not been built yet.",
                    "fix": [
                        "cd dashboard",
                        "npm install",
                        "npm run build",
                        "then restart the application",
                    ],
                    "api_is_available": True,
                    "api_docs": "/api/docs",
                }
            },
        )


app = create_app()


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)

    exposure = network_exposure_warning(settings.host)
    if exposure:
        log.warning(exposure)

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
