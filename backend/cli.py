"""``av-scout`` command-line entry point.

Deliberately small: the platform is dashboard-driven, so the CLI covers only
what a terminal is genuinely better at - starting the service, initialising the
database, checking the environment, and running the pipeline headlessly for CI.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from typing import Any

from backend.settings import display_host, get_settings, network_exposure_warning
from backend.version import SOFTWARE_VERSION


def _open_browser_later(url: str, delay: float = 2.0) -> None:
    def opener() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - headless environments
            pass

    threading.Thread(target=opener, daemon=True).start()


def cmd_start(args: argparse.Namespace) -> int:
    import uvicorn

    from backend.database.init_db import initialize_database

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    initialize_database()

    url = f"http://{display_host(host)}:{port}"
    print(f"AV Test Automation Platform {SOFTWARE_VERSION}")
    print(f"  mode:               {settings.operating_mode}")
    print(f"  source access:      {settings.source_access_mode}")
    print(f"  prod submission:    {'ENABLED' if settings.allow_production_submission else 'DISABLED'}")
    print(f"  dashboard:          {url}")
    print(f"  API docs:           {url}/api/docs")

    exposure = network_exposure_warning(host)
    if exposure:
        print(f"\n  WARNING: {exposure}")

    print("\nNo data-source query is started automatically.\n")

    if not args.no_browser and settings.open_browser_on_start:
        _open_browser_later(url)

    uvicorn.run("backend.main:app", host=host, port=port, log_level=settings.log_level.lower())
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    from backend.database.init_db import initialize_database

    result = initialize_database()
    print(json.dumps(result, indent=2))
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    from backend.api.system import environment

    report = environment()
    for check in report["checks"]:
        print(f"  [{check['status']:<7}] {check['name']}: {check['detail']}")
    summary = report["summary"]
    print(f"\n  {summary['pass']} pass, {summary['warning']} warning, {summary['fail']} fail")
    return 0 if summary["ready"] else 1


def cmd_connections(_: argparse.Namespace) -> int:
    from backend.connectors.registry import ConnectionManager
    from backend.database.init_db import initialize_database
    from backend.database.session import session_scope

    initialize_database()
    with session_scope() as session:
        results = ConnectionManager(session).test_all()
    for connection_id, status in sorted(results.items()):
        marker = "OK  " if status.connected else "FAIL"
        print(f"  [{marker}] {connection_id}: {status.status} - {status.message.splitlines()[0]}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Headless run, used by CI against the local adapter."""
    from backend.database.init_db import initialize_database
    from backend.models.contracts import RunRequest, ScoutQuery
    from backend.workers.runner import get_run_manager

    initialize_database()
    query = ScoutQuery(country_code=args.country, object_types=args.object or [])
    request = RunRequest(
        query=query,
        connection_id=args.connection,
        dry_run=not args.execute,
        limit=args.limit,
        csv_template_id=args.template,
    )

    manager = get_run_manager()
    if args.preview_only:
        print(json.dumps(manager.preview(request).model_dump(mode="json"), indent=2, default=str))
        return 0

    run_id = manager.create_run(request, "cli", "administrator")
    manager.start(run_id, "cli", "administrator")
    print(f"run {run_id} started ({'execute' if args.execute else 'dry-run'})")

    while manager.is_active(run_id):
        time.sleep(0.5)

    from sqlalchemy import select

    from backend.database.session import session_scope
    from backend.models.orm import AutomationRun

    with session_scope() as session:
        run = session.scalar(select(AutomationRun).where(AutomationRun.run_id == run_id))
        payload: dict[str, Any] = {
            "run_id": run_id,
            "status": run.status if run else "UNKNOWN",
            "records_processed": run.records_processed if run else 0,
            "blocking_error_count": run.blocking_error_count if run else 0,
            "csv_rows_created": run.csv_rows_created if run else 0,
            "output_dir": run.output_dir if run else None,
            "message": run.message if run else None,
        }
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "COMPLETED" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="av-scout", description="AV Test Automation Platform")
    parser.add_argument("--version", action="version", version=SOFTWARE_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start the backend and open the dashboard")
    start.add_argument("--host")
    start.add_argument("--port", type=int)
    start.add_argument("--no-browser", action="store_true")
    start.set_defaults(func=cmd_start)

    sub.add_parser("init-db", help="Create the schema and seed built-in profiles").set_defaults(func=cmd_init_db)
    sub.add_parser("check", help="Run the environment checks").set_defaults(func=cmd_check)
    sub.add_parser("connections", help="Test every configured connection").set_defaults(func=cmd_connections)

    run = sub.add_parser("run", help="Execute a run headlessly")
    run.add_argument("--country", default=None, help="ISO country code, e.g. DE")
    run.add_argument("--object", action="append", help="Object type filter (repeatable)")
    run.add_argument("--connection", default=None, help="Connection id")
    run.add_argument("--template", default="germany_bus_test")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--execute", action="store_true", help="Run for real instead of a dry run")
    run.add_argument("--preview-only", action="store_true", help="Print the query preview and exit")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
