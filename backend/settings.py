"""Application settings.

Precedence (highest wins):

    1. environment variables (``AV_*``)
    2. ``.env`` file in the project root (approved local development only)
    3. ``config/base.yaml``
    4. hard-coded defaults in this module

Secrets are NEVER read from ``config/base.yaml``. They come from the
environment or the OS credential store (see :mod:`backend.auth.secrets`).
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"
ENV_FILE = PROJECT_ROOT / ".env"


# ---------------------------------------------------------------------------
# .env loading (no external dependency; deliberately minimal)
# ---------------------------------------------------------------------------
def load_dotenv(path: Path = ENV_FILE) -> None:
    """Populate ``os.environ`` from a ``.env`` file without overwriting it.

    Real environment variables always win so that a corporate secret injector
    cannot be shadowed by a stale local file.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------
class DataScoutSettings(BaseModel):
    """Connection settings for the NVIDIA / internal Data Scout adapter.

    ``enabled`` is False until an administrator supplies the approved endpoint.
    The adapter refuses to operate - and never fabricates results - while
    unconfigured.
    """

    enabled: bool = False
    base_url: str | None = None
    auth_mode: str = "none"  # none | bearer | api_key | oauth_client_credentials
    integration_type: str = "rest_api"  # rest_api | sdk | graphql | cli | database | csv_export | json_export | browser
    verify_tls: bool = True
    timeout_seconds: int = 60
    # Secret material is resolved lazily from the credential store, never
    # serialised into profiles, logs or API responses.
    token_env_var: str = "AV_DATASCOUT_TOKEN"
    api_key_env_var: str = "AV_DATASCOUT_API_KEY"

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.base_url)


class Settings(BaseModel):
    """Resolved application settings."""

    # --- identity -------------------------------------------------------
    app_name: str = "AV Test Automation Platform"
    software_version: str = "1.0.0"
    contract_version: str = "1.0.0"
    environment: str = "development"

    # --- server ---------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    open_browser_on_start: bool = True
    dev_origins: list[str] = Field(default_factory=list)

    # --- operating mode -------------------------------------------------
    operating_mode: str = "production"  # production | demo
    source_access_mode: str = "read_only"
    allow_production_submission: bool = False
    allow_browser_automation: bool = False
    force_dry_run_on_first_execution: bool = True

    # --- storage --------------------------------------------------------
    database_url: str = "sqlite:///./data/local.db"
    checkpoint_dir: Path = PROJECT_ROOT / "data" / "checkpoints"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    output_dir: Path = PROJECT_ROOT / "output"
    retention_days: int = 180

    # --- processing -----------------------------------------------------
    max_events_per_run: int = 5000
    page_size: int = 100
    worker_concurrency: int = 4
    compute_device: str = "auto"
    checkpoint_interval: int = 10
    consecutive_error_limit: int = 10

    # --- identity of the desktop operator -------------------------------
    local_user: str = "local.tester"
    local_role: str = "tester"

    # --- adapters -------------------------------------------------------
    data_scout: DataScoutSettings = Field(default_factory=DataScoutSettings)
    # Deliberately has NO default. A default pointing at tests/golden_dataset
    # made the local-files adapter serve test fixtures as production events
    # whenever nobody had configured a real source - the platform's worst
    # possible failure mode, because the fabricated results were indistinguish-
    # able from real ones downstream. An unset source is an unset source.
    local_dataset_dir: Path | None = None
    map_service_url: str | None = None
    object_store_url: str | None = None

    # --- raw config sections (engines read their own tuning here) -------
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    # -- convenience ------------------------------------------------------
    @property
    def is_demo_mode(self) -> bool:
        return self.operating_mode.lower() == "demo"

    @property
    def is_production_mode(self) -> bool:
        return not self.is_demo_mode

    def section(self, name: str) -> dict[str, Any]:
        """Return a raw config section (``geometry``, ``behavior``, ...)."""
        value = self.raw.get(name, {})
        return value if isinstance(value, dict) else {}

    def ensure_directories(self) -> None:
        for path in (self.checkpoint_dir, self.cache_dir, self.output_dir):
            path.mkdir(parents=True, exist_ok=True)
        db_path = sqlite_path_from_url(self.database_url)
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Bind-address helpers
# ---------------------------------------------------------------------------
# Parsed with `ipaddress` rather than compared against literal strings. That is
# both more correct - it recognises ``::``, ``::1`` and the whole 127.0.0.0/8
# range, not just the two spellings anyone remembers - and it keeps a bare
# "0.0.0.0" literal out of the source, which static analysers flag as a
# hardcoded bind-all regardless of the surrounding context.
def _parse_host(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip())
    except ValueError:
        return None  # a hostname such as "localhost" or "av-laptop.corp"


def is_loopback_host(host: str) -> bool:
    """True when binding ``host`` exposes the service only to this machine."""
    address = _parse_host(host)
    if address is None:
        return host.strip().lower() in {"localhost", ""}
    return address.is_loopback


def binds_all_interfaces(host: str) -> bool:
    """True when ``host`` is the unspecified address, i.e. every interface."""
    address = _parse_host(host)
    return address is not None and address.is_unspecified


def display_host(host: str) -> str:
    """The host to show in a browsable URL.

    A service bound to every interface, or to loopback, is reached from this
    machine as ``localhost``; printing the raw bind address would give the
    tester a URL that is confusing (``http://LocalHost:8000``) or simply not
    openable (``http://0.0.0.0:8000``).

    Delegates to the two predicates above rather than re-testing the address,
    so the three functions cannot drift apart on some spelling one of them
    handles and another does not.
    """
    if is_loopback_host(host) or binds_all_interfaces(host):
        return "localhost"
    return host.strip()


def network_exposure_warning(host: str) -> str | None:
    """Warn when the configured bind address reaches beyond this machine.

    The platform's posture is that this dashboard is a local desktop tool
    holding AV data, and must not be casually reachable from the corporate LAN.
    Binding wider than loopback is allowed, but never silently.
    """
    if is_loopback_host(host):
        return None
    if binds_all_interfaces(host):
        return (
            f"Binding to {host} exposes this dashboard on EVERY network interface. "
            "It holds AV review data and has no network authentication of its own. "
            "Set AV_HOST=127.0.0.1 unless an approved deployment requires otherwise."
        )
    return (
        f"Binding to {host} exposes this dashboard beyond this machine. "
        "Set AV_HOST=127.0.0.1 unless an approved deployment requires otherwise."
    )


def sqlite_path_from_url(url: str) -> Path | None:
    """Return the on-disk path for a SQLite URL, or ``None`` for other engines."""
    if not url.startswith("sqlite"):
        return None
    _, _, tail = url.partition("///")
    if not tail or tail == ":memory:":
        return None
    candidate = Path(tail)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    return candidate


def _resolve_path(value: str | Path, default: Path) -> Path:
    if value in (None, ""):
        return default
    path = Path(str(value))
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _optional_path(value: str | Path) -> Path | None:
    """Resolve a path, or ``None`` when nothing was configured.

    Distinct from :func:`_resolve_path` because for a data source "unset" is a
    meaningful state that must survive to the adapter, rather than collapsing
    into some fallback directory.
    """
    if value in (None, ""):
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


# The committed test fixtures. Reading these is legitimate - the suite and CI
# both do it deliberately - but a dataset served from here is never real AV
# data, so anything pointed at it must say so rather than present the results
# as production output.
FIXTURE_ROOT = PROJECT_ROOT / "tests"


def is_fixture_dataset(path: Path | str | None) -> bool:
    """True when ``path`` lies inside the repository's test-fixture tree."""
    if path is None:
        return False
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    try:
        resolved.relative_to(FIXTURE_ROOT.resolve())
    except ValueError:
        return False
    return True


def build_settings() -> Settings:
    """Compose settings from YAML + environment."""
    load_dotenv()

    data: dict[str, Any] = {}
    if BASE_CONFIG_PATH.is_file():
        loaded = yaml.safe_load(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded

    app = data.get("application", {})
    server = data.get("server", {})
    mode = data.get("mode", {})
    storage = data.get("storage", {})
    processing = data.get("processing", {})

    ds_enabled = _env_bool("AV_DATASCOUT_ENABLED", False)
    data_scout = DataScoutSettings(
        enabled=ds_enabled,
        base_url=_env_str("AV_DATASCOUT_BASE_URL"),
        auth_mode=_env_str("AV_DATASCOUT_AUTH_MODE", "none") or "none",
        integration_type=_env_str("AV_DATASCOUT_INTEGRATION_TYPE", "rest_api") or "rest_api",
        verify_tls=_env_bool("AV_DATASCOUT_VERIFY_TLS", True),
        timeout_seconds=_env_int("AV_DATASCOUT_TIMEOUT_SECONDS", 60),
    )

    settings = Settings(
        app_name=app.get("name", "AV Test Automation Platform"),
        software_version=app.get("software_version", "1.0.0"),
        contract_version=app.get("contract_version", "1.0.0"),
        environment=_env_str("AV_ENV", "development") or "development",
        host=_env_str("AV_HOST", server.get("host", "127.0.0.1")) or "127.0.0.1",
        port=_env_int("AV_PORT", int(server.get("port", 8000))),
        log_level=(_env_str("AV_LOG_LEVEL", "INFO") or "INFO").upper(),
        open_browser_on_start=bool(server.get("open_browser_on_start", True)),
        dev_origins=list(server.get("dev_origins", [])),
        operating_mode=(_env_str("AV_MODE", mode.get("operating_mode", "production")) or "production").lower(),
        source_access_mode=_env_str("AV_SOURCE_ACCESS_MODE", mode.get("source_access_mode", "read_only"))
        or "read_only",
        allow_production_submission=_env_bool(
            "AV_ALLOW_PRODUCTION_SUBMISSION", bool(mode.get("allow_production_submission", False))
        ),
        allow_browser_automation=_env_bool(
            "AV_ALLOW_BROWSER_AUTOMATION", bool(mode.get("allow_browser_automation", False))
        ),
        force_dry_run_on_first_execution=bool(mode.get("force_dry_run_on_first_execution", True)),
        database_url=_env_str("AV_DATABASE_URL", storage.get("database_url", "sqlite:///./data/local.db"))
        or "sqlite:///./data/local.db",
        checkpoint_dir=_resolve_path(storage.get("checkpoint_dir", ""), PROJECT_ROOT / "data" / "checkpoints"),
        cache_dir=_resolve_path(storage.get("cache_dir", ""), PROJECT_ROOT / "data" / "cache"),
        output_dir=_resolve_path(
            _env_str("AV_EVIDENCE_DIR", storage.get("output_dir", "")) or "", PROJECT_ROOT / "output"
        ),
        retention_days=int(storage.get("retention_days", 180)),
        max_events_per_run=int(processing.get("max_events_per_run", 5000)),
        page_size=int(processing.get("page_size", 100)),
        worker_concurrency=int(processing.get("worker_concurrency", 4)),
        compute_device=str(processing.get("compute_device", "auto")),
        checkpoint_interval=int(processing.get("checkpoint_interval", 10)),
        consecutive_error_limit=int(processing.get("consecutive_error_limit", 10)),
        local_user=_env_str("AV_LOCAL_USER", "local.tester") or "local.tester",
        local_role=_env_str("AV_LOCAL_ROLE", data.get("roles", {}).get("default_role", "tester")) or "tester",
        data_scout=data_scout,
        local_dataset_dir=_optional_path(_env_str("AV_LOCAL_DATASET_DIR", "") or ""),
        map_service_url=_env_str("AV_MAP_SERVICE_URL"),
        object_store_url=_env_str("AV_OBJECT_STORE_URL"),
        raw=data,
    )
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()


def reset_settings_cache() -> None:
    """Used by tests and by the Administration page after a config change."""
    get_settings.cache_clear()
