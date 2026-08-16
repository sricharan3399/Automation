#!/usr/bin/env python
"""Remove non-production records from the local database (spec sections 50, 51).

A fresh installation must start empty so the dashboard shows ``0`` rather than
leftovers from development. This removes records that demonstrably came from a
non-production source, and refuses to guess about anything else.

**Dry run by default.** Nothing is deleted without ``--confirm``, and
``--confirm`` takes a backup of the database file first.

Classification, per run:

``NON_PRODUCTION``
    The run used the synthetic adapter, or used a local dataset directory
    inside the repository's test-fixture tree. These are safe to remove.

``QUESTIONABLE``
    Provenance cannot be established from the record. **Never deleted.**
    Section 50 is explicit: if demo cannot be distinguished from real, report
    it rather than deleting blind.

``PRODUCTION``
    Came from a real configured source. Never touched.

Tester review decisions are counted and reported separately, because losing a
human judgement is worse than keeping a stale row. Use ``--include-reviewed``
to remove non-production runs that carry reviews; without it they are held back.

Usage::

    .venv\\Scripts\\python.exe scripts\\purge_non_production_records.py
    .venv\\Scripts\\python.exe scripts\\purge_non_production_records.py --confirm
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from backend.database.session import session_scope  # noqa: E402
from backend.models.orm import (  # noqa: E402
    AutomationRun,
    Detection,
    EgoPose,
    Event,
    Evidence,
    FieldRecommendation,
    MapFeature,
    Review,
    SensorStream,
    ValidationResult,
)
from backend.settings import get_settings, is_fixture_dataset, sqlite_path_from_url  # noqa: E402

NON_PRODUCTION = "NON_PRODUCTION"
QUESTIONABLE = "QUESTIONABLE"
PRODUCTION = "PRODUCTION"

# Adapters that can never produce production data.
_SYNTHETIC_ADAPTERS = {"synthetic"}


def classify(run: AutomationRun, fixture_connections: set[str]) -> tuple[str, str]:
    """Return ``(classification, reason)`` for one run."""
    if run.adapter_name in _SYNTHETIC_ADAPTERS:
        return NON_PRODUCTION, "synthetic adapter"
    if run.connection_profile_id in fixture_connections:
        return NON_PRODUCTION, f"connection '{run.connection_profile_id}' reads the test-fixture tree"
    if run.adapter_name is None:
        return QUESTIONABLE, "no adapter recorded on the run"
    return PRODUCTION, f"adapter '{run.adapter_name}'"


def _fixture_connections(session) -> set[str]:
    """Connections whose dataset directory is inside tests/.

    Includes the environment default, because an adapter with no explicit
    directory falls back to it - which is how fixture data reached production
    in the first place.
    """
    from backend.models.orm import ConnectionProfile

    fixture: set[str] = set()
    env_default_is_fixture = is_fixture_dataset(get_settings().local_dataset_dir)

    for profile in session.scalars(select(ConnectionProfile)).all():
        dataset_dir = (profile.settings_json or {}).get("dataset_dir")
        if is_fixture_dataset(dataset_dir):
            fixture.add(profile.connection_id)
        elif dataset_dir in (None, "") and profile.adapter == "local_files" and env_default_is_fixture:
            fixture.add(profile.connection_id)
    return fixture


def _backup_database() -> Path | None:
    """Copy the SQLite file aside before any deletion."""
    db_path = sqlite_path_from_url(get_settings().database_url)
    if db_path is None or not db_path.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{db_path.stem}.{stamp}.pre-purge.db"
    shutil.copy2(db_path, destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove non-production records from the local database")
    parser.add_argument("--confirm", action="store_true", help="actually delete (default is a dry run)")
    parser.add_argument(
        "--include-reviewed",
        action="store_true",
        help="also remove non-production runs that carry tester review decisions",
    )
    args = parser.parse_args(argv)

    print("=" * 57)
    print("NON-PRODUCTION RECORD PURGE")
    print("=" * 57)
    print(f"  database : {get_settings().database_url}")
    print(f"  mode     : {'DELETE' if args.confirm else 'DRY RUN (nothing will be deleted)'}")
    print()

    with session_scope() as session:
        fixture_connections = _fixture_connections(session)
        runs = list(session.scalars(select(AutomationRun)).all())

        if not runs:
            print("  No runs in the database. Nothing to purge.")
            return 0

        buckets: dict[str, list[tuple[AutomationRun, str]]] = {
            NON_PRODUCTION: [],
            QUESTIONABLE: [],
            PRODUCTION: [],
        }
        for run in runs:
            classification, reason = classify(run, fixture_connections)
            buckets[classification].append((run, reason))

        print("  Runs by classification:")
        for name in (NON_PRODUCTION, QUESTIONABLE, PRODUCTION):
            print(f"    {name:<16}{len(buckets[name])}")
        print()

        for run, reason in buckets[NON_PRODUCTION]:
            print(f"    remove  {run.run_id}  ({reason}, {run.records_processed} record(s))")
        for run, reason in buckets[QUESTIONABLE]:
            print(f"    KEEP    {run.run_id}  QUESTIONABLE: {reason}")
        for run, reason in buckets[PRODUCTION]:
            print(f"    keep    {run.run_id}  ({reason})")
        print()

        removable_pks = {run.run_pk for run, _ in buckets[NON_PRODUCTION]}
        if not removable_pks:
            print("  Nothing classified as non-production. Database unchanged.")
            return 0

        # Events belong to a run through first_run_pk/last_run_pk. An event
        # touched by a production run is never removed, even if some earlier
        # non-production run also saw it.
        event_pks = set(
            session.scalars(
                select(Event.event_pk).where(
                    Event.first_run_pk.in_(removable_pks), Event.last_run_pk.in_(removable_pks)
                )
            ).all()
        )

        reviewed = session.scalar(
            select(func.count()).select_from(Review).where(Review.event_pk.in_(event_pks or {-1}))
        )
        if reviewed and not args.include_reviewed:
            print(f"  HELD BACK: {reviewed} tester review decision(s) attach to these events.")
            print("  A human judgement is harder to recreate than a test run, so nothing")
            print("  was deleted. Re-run with --include-reviewed if you are certain.")
            return 0

        counts = {
            "detections": session.scalar(
                select(func.count()).select_from(Detection).where(Detection.event_pk.in_(event_pks or {-1}))
            ),
            "ego_poses": session.scalar(
                select(func.count()).select_from(EgoPose).where(EgoPose.event_pk.in_(event_pks or {-1}))
            ),
            "sensor_streams": session.scalar(
                select(func.count()).select_from(SensorStream).where(SensorStream.event_pk.in_(event_pks or {-1}))
            ),
            "map_features": session.scalar(
                select(func.count()).select_from(MapFeature).where(MapFeature.event_pk.in_(event_pks or {-1}))
            ),
            "validation_results": session.scalar(
                select(func.count()).select_from(ValidationResult).where(
                    ValidationResult.event_pk.in_(event_pks or {-1})
                )
            ),
            "field_recommendations": session.scalar(
                select(func.count()).select_from(FieldRecommendation).where(
                    FieldRecommendation.event_pk.in_(event_pks or {-1})
                )
            ),
            "evidence": session.scalar(
                select(func.count()).select_from(Evidence).where(Evidence.event_pk.in_(event_pks or {-1}))
            ),
            "reviews": reviewed,
            "events": len(event_pks),
            "automation_runs": len(removable_pks),
        }

        print("  Rows that will be removed:")
        for table, count in counts.items():
            print(f"    {table:<24}{count}")
        print(f"    {'TOTAL':<24}{sum(counts.values())}")
        print()

        if not args.confirm:
            print("  DRY RUN - nothing was deleted.")
            print("  Re-run with --confirm to apply (a backup is taken automatically).")
            return 0

    backup = _backup_database()
    if backup is None:
        print("  Could not locate a SQLite file to back up. Refusing to delete.")
        return 1
    print(f"  Backup written: {backup}")

    with session_scope() as session:
        # Children first: no ON DELETE CASCADE is declared, so ordering matters.
        for model in (
            Detection,
            EgoPose,
            SensorStream,
            MapFeature,
            ValidationResult,
            FieldRecommendation,
            Evidence,
            Review,
        ):
            session.query(model).filter(model.event_pk.in_(event_pks or {-1})).delete(synchronize_session=False)
        session.query(Event).filter(Event.event_pk.in_(event_pks or {-1})).delete(synchronize_session=False)
        session.query(AutomationRun).filter(AutomationRun.run_pk.in_(removable_pks)).delete(
            synchronize_session=False
        )

    with session_scope() as session:
        remaining = {
            "automation_runs": session.scalar(select(func.count()).select_from(AutomationRun)),
            "events": session.scalar(select(func.count()).select_from(Event)),
            "validation_results": session.scalar(select(func.count()).select_from(ValidationResult)),
            "reviews": session.scalar(select(func.count()).select_from(Review)),
        }
    print()
    print("  Remaining after purge:")
    for table, count in remaining.items():
        print(f"    {table:<24}{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
