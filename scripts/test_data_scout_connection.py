#!/usr/bin/env python
"""Read-only Data Scout connection test (spec section 116).

Runs the smallest set of operations that proves the adapter can actually talk
to the source: authenticate, read capabilities, read the schema, and fetch a
tiny page of metadata. Nothing is written, nothing is submitted, and no bulk
retrieval happens.

Uses the adapter configuration exactly as the application does - the same
connection profile, the same credential resolution - so a PASS here means the
dashboard will work, not that a parallel code path happened to succeed.

Exit codes::

    0  every executed check passed
    1  a check failed
    2  the adapter is not configured (expected before the approved details
       arrive; not a failure of the platform)

Usage::

    .venv\\Scripts\\python.exe scripts\\test_data_scout_connection.py
    .venv\\Scripts\\python.exe scripts\\test_data_scout_connection.py --connection nvidia_data_scout
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow running as a plain script from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.connectors.base import AdapterError, AdapterNotConfigured  # noqa: E402
from backend.database.init_db import initialize_database  # noqa: E402
from backend.database.session import session_scope  # noqa: E402
from backend.models.contracts import ScoutQuery  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# A deliberately tiny probe query. Section 117: the first real-data validation
# is five events of metadata, not a bulk pull.
PROBE_LIMIT = 5


@dataclass
class Result:
    name: str
    status: str
    detail: str


def _run(name: str, operation: Callable[[], str]) -> Result:
    """Execute one check, converting adapter errors into a reportable result."""
    try:
        return Result(name, PASS, operation())
    except AdapterNotConfigured as exc:
        return Result(name, SKIP, exc.user_message.splitlines()[0])
    except AdapterError as exc:
        return Result(name, FAIL, exc.user_message.splitlines()[0])
    except Exception as exc:  # noqa: BLE001 - a report is more useful than a traceback
        return Result(name, FAIL, f"{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Data Scout connection test")
    parser.add_argument("--connection", default="nvidia_data_scout", help="connection id to test")
    parser.add_argument("--country", default="DE", help="country code for the probe query")
    parser.add_argument("--object", default="bus", help="object type for the probe query")
    args = parser.parse_args(argv)

    print("=" * 57)
    print("DATA SCOUT CONNECTION TEST")
    print("=" * 57)
    print(f"  connection : {args.connection}")
    print("  mode       : READ ONLY (no write, no submit, no bulk retrieval)")
    print()

    initialize_database()

    from backend.connectors.registry import ConnectionManager

    with session_scope() as session:
        manager = ConnectionManager(session)
        try:
            adapter = manager.adapter_for(args.connection)
        except AdapterError as exc:
            print(f"  Adapter unavailable: {exc.user_message}")
            return 2

        missing = getattr(adapter, "missing_configuration", lambda: [])()
        if missing:
            print("  DATA SCOUT: NOT CONFIGURED")
            print()
            print("  Missing configuration:")
            for item in missing:
                print(f"    - {item}")
            print()
            print("  This is the expected state until the approved interface details")
            print("  are supplied. See docs/DATA_SCOUT_REAL_CONNECTION.md.")
            print()
            print("  Production Readiness: WAITING FOR DATA SOURCE")
            return 2

        results: list[Result] = []
        started = time.perf_counter()

        results.append(_run("Authentication", lambda: _authenticate(adapter)))
        # Everything after authentication is pointless if it failed.
        if results[0].status == PASS:
            results.append(_run("Capabilities", lambda: _capabilities(adapter)))
            results.append(_run("Schema", lambda: _schema(adapter)))
            results.append(_run("Read permission", lambda: _permissions(adapter)))
            results.append(_run("Count query", lambda: _count(adapter, args)))
            results.append(_run("Small event query", lambda: _small_query(adapter, args)))
        else:
            for name in ("Capabilities", "Schema", "Read permission", "Count query", "Small event query"):
                results.append(Result(name, SKIP, "skipped: authentication did not succeed"))

        elapsed = (time.perf_counter() - started) * 1000.0

    for result in results:
        print(f"  {result.name + ':':<20}{result.status}   {result.detail}")

    failed = [r for r in results if r.status == FAIL]
    print()
    print(f"  Total elapsed: {elapsed:.0f} ms")
    print()
    if failed:
        print("  Production Readiness:")
        print("  NOT READY")
        print()
        print(f"  {len(failed)} check(s) failed. Resolve the errors above before running a batch.")
        return 1

    print("  Production Readiness:")
    print("  READY")
    print()
    print("  Next: run 5 events of metadata only and compare every field against")
    print("  Data Scout by hand before increasing the batch size (section 117).")
    return 0


# ---------------------------------------------------------------------------
# Individual probes - each returns a human-readable detail string on success
# ---------------------------------------------------------------------------
def _authenticate(adapter: Any) -> str:
    started = time.perf_counter()
    adapter.authenticate()
    return f"authenticated in {(time.perf_counter() - started) * 1000:.0f} ms"


def _capabilities(adapter: Any) -> str:
    status = adapter.test_connection()
    if not status.connected:
        raise AdapterError(status.message, user_message=status.message)
    latency = f"{status.latency_ms:.0f} ms" if status.latency_ms is not None else "n/a"
    return f"{status.status}, latency {latency}"


def _schema(adapter: Any) -> str:
    schema = adapter.get_schema()
    return f"{len(schema.fields)} field(s), version {schema.schema_version or 'unreported'}"


def _permissions(adapter: Any) -> str:
    status = adapter.test_connection()
    permissions = list(status.permissions) or ["(none reported)"]
    # Write access is reported, never exercised.
    writable = any("write" in p.lower() for p in permissions)
    suffix = "  WARNING: write access detected; the platform stays read-only." if writable else ""
    return ", ".join(permissions) + suffix


def _count(adapter: Any, args: argparse.Namespace) -> str:
    query = ScoutQuery(country_code=args.country, object_types=[args.object])
    count, exact, source = adapter.estimate_count(query)
    if count is None:
        # Not a failure: the source may genuinely not offer counting.
        return f"COUNT NOT AVAILABLE UNTIL QUERY EXECUTION (source: {source})"
    return f"{count} matching event(s), {'exact' if exact else 'estimated'} (source: {source})"


def _small_query(adapter: Any, args: argparse.Namespace) -> str:
    query = ScoutQuery(country_code=args.country, object_types=[args.object])
    page = adapter.search_events(query, limit=PROBE_LIMIT)
    if not page.event_ids:
        # Zero is a real answer, and the honest one.
        return f"0 EVENTS FOUND for country={args.country} object={args.object}"
    # Metadata only - no sensor payload, no frames, no trajectory.
    metadata = adapter.get_event_metadata(page.event_ids[0])
    return (
        f"{len(page.event_ids)} event id(s) returned; "
        f"first event metadata resolved (event_id={metadata.event_id})"
    )


if __name__ == "__main__":
    sys.exit(main())
