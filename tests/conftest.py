"""Shared fixtures.

Every test runs against an isolated temporary database, output directory and
checkpoint directory, so the suite never touches a developer's real ``data/``
or ``output/`` tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend import identity as identity_module
from backend.configstore import get_config_store
from backend.database import session as session_module
from backend.database.init_db import initialize_database
from backend.settings import PROJECT_ROOT, reset_settings_cache

GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden_dataset"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every filesystem and database path at a per-test temp directory."""
    monkeypatch.setenv("AV_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("AV_EVIDENCE_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("AV_LOCAL_DATASET_DIR", str(GOLDEN_DIR))
    monkeypatch.setenv("AV_MODE", "production")
    monkeypatch.setenv("AV_LOCAL_ROLE", "administrator")
    # A fixed salt keeps canonical keys and pseudonyms stable within a test run.
    monkeypatch.setenv("AV_REDACTION_SALT", "test-salt-do-not-use-in-production")

    reset_settings_cache()
    identity_module.reset_salt_cache()
    session_module.reset_engine()
    get_config_store().reload()

    yield

    session_module.reset_engine()
    reset_settings_cache()
    identity_module.reset_salt_cache()


@pytest.fixture()
def database():
    initialize_database()
    from backend.database.session import session_scope

    return session_scope


@pytest.fixture(scope="session")
def golden_documents() -> list[dict[str, Any]]:
    """The committed golden fixtures, or freshly generated ones if absent."""
    events_dir = GOLDEN_DIR / "events"
    if events_dir.is_dir():
        documents = [
            json.loads(path.read_text(encoding="utf-8")) for path in sorted(events_dir.glob("*.json"))
        ]
        if documents:
            return documents

    from backend.connectors.synthetic import generate_documents

    return generate_documents()


@pytest.fixture()
def local_adapter():
    from backend.connectors.local_files import LocalFilesAdapter

    adapter = LocalFilesAdapter({"dataset_dir": str(GOLDEN_DIR)})
    adapter.authenticate()
    return adapter


@pytest.fixture()
def clean_event(golden_documents: list[dict[str, Any]]) -> dict[str, Any]:
    """A golden event with no injected faults."""
    for document in golden_documents:
        if not document.get("injected_faults"):
            return document
    raise AssertionError("The golden dataset contains no fault-free event.")


def document_with_fault(documents: list[dict[str, Any]], fault: str) -> dict[str, Any]:
    for document in documents:
        if fault in (document.get("injected_faults") or []):
            return document
    raise AssertionError(f"The golden dataset contains no event with the '{fault}' fault.")
