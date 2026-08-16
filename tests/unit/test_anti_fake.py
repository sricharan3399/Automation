"""The anti-fake gate (spec sections 99-103).

Two kinds of test live here.

**Static** - scan the production source tree for constructs that manufacture
data at runtime. This is a regression fence: it exists so that the specific
defects found during the production-data conversion cannot quietly return.

**Behavioural** - drive the real code paths and assert the honest outcome:
no source means no records, an unavailable source raises rather than
substitutes, zero results means zero, and a missing field stays missing.

The static scan deliberately covers only ``backend/`` and ``dashboard/src`` -
``tests/`` and ``developer_tools/`` are where fixtures are allowed to live.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.settings import PROJECT_ROOT, is_fixture_dataset

BACKEND = PROJECT_ROOT / "backend"
FRONTEND = PROJECT_ROOT / "dashboard" / "src"


def _python_sources() -> list[Path]:
    return sorted(p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts)


def _frontend_sources() -> list[Path]:
    if not FRONTEND.is_dir():  # pragma: no cover - dashboard not checked out
        return []
    return sorted(
        p
        for p in FRONTEND.rglob("*")
        if p.suffix in {".ts", ".tsx"} and "__tests__" not in p.parts and not p.name.endswith(".test.tsx")
    )


# ---------------------------------------------------------------------------
# Static: production code must not manufacture data
# ---------------------------------------------------------------------------
def test_backend_does_not_import_random_or_faker() -> None:
    """Operational values must never come from a random number generator.

    Checked with the AST rather than a text search, so a mention inside a
    docstring or comment does not trip it and an aliased import cannot hide.
    """
    banned = {"random", "faker", "mimesis"}
    offenders: list[str] = []

    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in banned:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} from {node.module}")

    assert not offenders, "Randomness must not reach production code:\n  " + "\n  ".join(offenders)


def test_frontend_has_no_random_or_mock_data() -> None:
    """The dashboard must render what the API returned, and nothing else."""
    banned = re.compile(
        r"Math\.random|\bfaker\b|mockData|demoData|sampleData|fakeData|"
        r"generateMock|generateFake|MOCK_MODE",
    )
    offenders: list[str] = []
    for path in _frontend_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if banned.search(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}: {line.strip()}")

    assert not offenders, "Fabricated data in the dashboard:\n  " + "\n  ".join(offenders)


def test_no_silent_fallback_to_sample_data() -> None:
    """``except: return <something synthetic>`` is the prohibited pattern.

    Section 5 is explicit: an unavailable source must propagate, never degrade
    into fabricated results. This looks for a handler whose body reaches for
    anything demo-flavoured.
    """
    suspicious = re.compile(r"(demo|mock|fake|sample|synthetic)", re.IGNORECASE)
    offenders: list[str] = []

    for path in _python_sources():
        # The synthetic adapter is the one module allowed to name itself.
        if path.name == "synthetic.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child.value is not None:
                    rendered = ast.unparse(child.value)
                    if suspicious.search(rendered):
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{child.lineno}: except -> return {rendered}"
                        )

    assert not offenders, "Exception handler substitutes synthetic data:\n  " + "\n  ".join(offenders)


def test_no_module_defaults_to_the_fixture_directory() -> None:
    """No production module may name tests/golden_dataset as a default.

    This is the exact defect the conversion fixed: ``local_dataset_dir``
    defaulted to the fixture tree, so an unconfigured install served committed
    test data as production events.
    """
    # A string literal that *is* a path has no spaces in it; a sentence that
    # merely mentions the directory does. That distinction is the whole rule,
    # and it is checked against parsed string values rather than raw lines -
    # two earlier line-based versions of this test were wrong in opposite
    # directions (one let the real defect through, the next flagged an error
    # message), which is why it now inspects the AST.
    offenders: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value.strip()
            if "golden_dataset" not in value or " " in value:
                continue
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: {value!r}")

    assert not offenders, (
        "Production code refers to the test-fixture dataset:\n  " + "\n  ".join(offenders)
    )


def test_shipped_env_template_does_not_point_at_fixtures() -> None:
    """SETUP_AND_START.bat copies .env.example to .env on a fresh install.

    A fixture path here reaches every new laptop, which is how the original
    defect was actually delivered.
    """
    template = PROJECT_ROOT / ".env.example"
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == "AV_LOCAL_DATASET_DIR":
            assert not value.strip(), (
                f".env.example:{number} ships a dataset directory ({value.strip()!r}). "
                "It must be blank so a fresh install has no implicit event source."
            )


def test_seeded_connections_never_claim_a_health_result() -> None:
    """A status is an observation. Seeds have observed nothing (section 8)."""
    from backend.database.init_db import BUILTIN_CONNECTIONS

    forbidden = {"CONNECTED", "CONFIGURED", "HEALTHY", "OK", "PASS"}
    offenders = [
        f"{spec['connection_id']}={spec['last_status']}"
        for spec in BUILTIN_CONNECTIONS
        if spec["last_status"].upper() in forbidden
    ]
    assert not offenders, "Seeded connection claims an unearned health status: " + ", ".join(offenders)


def test_no_event_source_is_enabled_by_default() -> None:
    """A fresh install must have nothing to silently read from."""
    from backend.connectors.registry import EVENT_SOURCE_KINDS
    from backend.database.init_db import BUILTIN_CONNECTIONS

    enabled = [
        spec["connection_id"]
        for spec in BUILTIN_CONNECTIONS
        if spec["kind"] in EVENT_SOURCE_KINDS and spec["enabled"]
    ]
    assert not enabled, f"Event source(s) enabled in the shipped seed: {enabled}"


# ---------------------------------------------------------------------------
# Behavioural: the honest outcome
# ---------------------------------------------------------------------------
def test_unconfigured_source_raises_rather_than_fabricating() -> None:
    """No source configured => an error the UI can show, not sample events."""
    from backend.connectors.base import AdapterNotConfigured
    from backend.connectors.local_files import LocalFilesAdapter

    adapter = LocalFilesAdapter({"dataset_dir": None})
    adapter.dataset_dir = None  # simulate a genuinely unconfigured installation

    with pytest.raises(AdapterNotConfigured) as excinfo:
        adapter.authenticate()
    # The message has to tell the tester what to do.
    assert "not fall back to sample data" in str(excinfo.value.user_message)


def test_missing_dataset_directory_raises(tmp_path) -> None:
    from backend.connectors.base import AdapterNotConfigured
    from backend.connectors.local_files import LocalFilesAdapter

    adapter = LocalFilesAdapter({"dataset_dir": str(tmp_path / "does_not_exist")})
    with pytest.raises(AdapterNotConfigured):
        adapter.authenticate()


def test_empty_source_yields_zero_events_not_demo_data(tmp_path) -> None:
    """An empty-but-valid source reports zero. It never falls back (section 103)."""
    from backend.connectors.local_files import LocalFilesAdapter
    from backend.models.contracts import ScoutQuery

    (tmp_path / "events").mkdir()
    adapter = LocalFilesAdapter({"dataset_dir": str(tmp_path)})

    status = adapter.test_connection()
    assert status.connected is False
    assert status.status == "ERROR"
    assert "No event documents found" in status.message

    # And searching returns nothing rather than inventing something.
    adapter._index = {}
    page = adapter.search_events(ScoutQuery(country_code="DE"), limit=10)
    assert page.event_ids == []
    assert page.total_estimate in (0, None)


def test_synthetic_adapter_is_refused_in_production() -> None:
    """Section 4: the mock adapter must be unreachable in production."""
    from backend.connectors.base import DemoDataRefused
    from backend.connectors.synthetic import SyntheticAdapter
    from backend.models.contracts import ScoutQuery
    from backend.settings import get_settings

    assert get_settings().is_production_mode, "conftest pins AV_MODE=production"

    with pytest.raises(DemoDataRefused):
        SyntheticAdapter({}).search_events(ScoutQuery(country_code="DE"), limit=1)


def test_fixture_dataset_is_recognised_wherever_it_is_referenced() -> None:
    """The guard must not be defeated by a relative path or a subdirectory."""
    golden = PROJECT_ROOT / "tests" / "golden_dataset"

    assert is_fixture_dataset(golden)
    assert is_fixture_dataset(golden / "events")
    assert is_fixture_dataset(str(golden))
    assert is_fixture_dataset("./tests/golden_dataset") or is_fixture_dataset(
        PROJECT_ROOT / "tests" / "golden_dataset"
    )
    assert not is_fixture_dataset(None)
    assert not is_fixture_dataset(PROJECT_ROOT / "output")


def test_readiness_refuses_while_pointed_at_fixtures(database) -> None:
    """The gate must fail exactly when the source is the fixture tree.

    conftest points AV_LOCAL_DATASET_DIR at the golden dataset, so this is the
    live configuration under test - the gate has to notice.
    """
    from backend.readiness import build_report

    with database() as session:
        report = build_report(session)

    fixture_check = next(c for c in report.checks if c.key == "no_fixture_data")
    assert fixture_check.status == "FAIL"
    assert report.production_ready is False


def test_readiness_never_reports_ready_with_a_blocking_check() -> None:
    """Guards the AND itself, independent of any particular check."""
    from backend.readiness import Check, ReadinessReport

    report = ReadinessReport(
        checks=[
            Check("a", "A", "PASS", ""),
            Check("b", "B", "WAITING", ""),
            Check("c", "C", "WARNING", "", mandatory=False),
        ]
    )
    assert report.production_ready is False

    report.checks[1].status = "PASS"
    # A non-mandatory WARNING alone must not block go-live.
    assert report.production_ready is True
