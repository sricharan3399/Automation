"""Health, environment and system-resource endpoints."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

import psutil
from fastapi import APIRouter
from sqlalchemy import text

from backend.api.deps import DbSession
from backend.auth.secrets import keyring_available
from backend.configstore import get_config_store
from backend.readiness import build_report
from backend.settings import PROJECT_ROOT, get_settings
from backend.version import CONTRACT_VERSION, METHOD_VERSION, SOFTWARE_VERSION

router = APIRouter(tags=["system"])

MIN_PYTHON = (3, 10)
RECOMMENDED_PYTHON = (3, 11)
MIN_RAM_GB = 4.0
MIN_DISK_GB = 5.0


def _check(name: str, ok: bool, detail: str, warning: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "status": "PASS" if ok else ("WARNING" if warning else "FAIL"),
        "detail": detail,
    }


def detect_gpu() -> dict[str, Any]:
    """Probe for an NVIDIA GPU without requiring any GPU dependency."""
    if shutil.which("nvidia-smi") is None:
        return {"available": False, "detail": "nvidia-smi was not found on PATH.", "devices": []}
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - platform dependent
        return {"available": False, "detail": f"nvidia-smi could not be executed: {exc}", "devices": []}

    if output.returncode != 0:
        return {"available": False, "detail": output.stderr.strip() or "nvidia-smi returned an error.", "devices": []}

    devices = []
    for line in output.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            devices.append({"name": parts[0], "memory": parts[1], "driver": parts[2] if len(parts) > 2 else None})

    cuda_available = False
    try:  # torch is optional and usually absent; absence is not an error
        import torch  # type: ignore[import-not-found]

        cuda_available = bool(torch.cuda.is_available())
    except Exception:
        cuda_available = False

    return {
        "available": bool(devices),
        "detail": f"{len(devices)} NVIDIA device(s) detected.",
        "devices": devices,
        "cuda_torch_available": cuda_available,
    }


@router.get("/health")
def health(session: DbSession) -> dict[str, Any]:
    """Liveness and readiness in one payload."""
    database_ok = True
    database_detail = "connected"
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - only on a broken database
        database_ok = False
        database_detail = str(exc)

    settings = get_settings()
    return {
        "status": "ok" if database_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "software_version": SOFTWARE_VERSION,
        "contract_version": CONTRACT_VERSION,
        "method_version": METHOD_VERSION,
        "rule_version": get_config_store().rule_version_signature(),
        "operating_mode": settings.operating_mode,
        "source_access_mode": settings.source_access_mode,
        "production_submission_enabled": settings.allow_production_submission,
        "database": {"ok": database_ok, "detail": database_detail, "url_scheme": settings.database_url.split(":")[0]},
    }


@router.get("/system/production-readiness")
def production_readiness(session: DbSession) -> dict[str, Any]:
    """The go-live gate (spec sections 95 and 96).

    Evaluated live on every call. Nothing is cached and no stored "ready" flag
    exists, so the answer cannot drift away from the actual system state.
    """
    report = build_report(session)
    payload = report.to_dict()
    payload["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    payload["software_version"] = SOFTWARE_VERSION
    return payload


@router.get("/system/environment")
def environment() -> dict[str, Any]:
    """First-run wizard: dependency and platform checks."""
    settings = get_settings()
    version = sys.version_info
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(PROJECT_ROOT))
    gpu = detect_gpu()

    checks = [
        _check(
            "Operating system",
            True,
            f"{platform.system()} {platform.release()} ({platform.machine()})",
        ),
        _check(
            "Python version",
            version >= MIN_PYTHON,
            f"{version.major}.{version.minor}.{version.micro}"
            + (
                ""
                if version >= RECOMMENDED_PYTHON
                else f" (3.11+ recommended; {version.major}.{version.minor} is supported)"
            ),
            warning=MIN_PYTHON <= version < RECOMMENDED_PYTHON,
        ),
        _check("CPU", True, f"{psutil.cpu_count(logical=False) or '?'} physical / {psutil.cpu_count()} logical cores"),
        _check(
            "RAM",
            memory.total / 1e9 >= MIN_RAM_GB,
            f"{memory.total / 1e9:.1f} GB total, {memory.available / 1e9:.1f} GB available",
        ),
        _check(
            "Free disk space",
            disk.free / 1e9 >= MIN_DISK_GB,
            f"{disk.free / 1e9:.1f} GB free on the platform drive",
        ),
        _check(
            "NVIDIA GPU",
            gpu["available"],
            gpu["detail"] + (" GPU acceleration is optional; metadata-only analysis runs on CPU." if not gpu["available"] else ""),
            warning=not gpu["available"],
        ),
        _check(
            "OS credential store",
            keyring_available(),
            "keyring is available for secret storage."
            if keyring_available()
            else "keyring is not installed; secrets must be injected through the environment.",
            warning=not keyring_available(),
        ),
        _check(
            "Configuration",
            (PROJECT_ROOT / "config" / "base.yaml").is_file(),
            "config/base.yaml found." if (PROJECT_ROOT / "config" / "base.yaml").is_file() else "config/base.yaml is missing.",
        ),
        _check(
            "Output directory",
            settings.output_dir.parent.exists(),
            f"Run outputs are written to {settings.output_dir}",
        ),
    ]

    for module in ("fastapi", "sqlalchemy", "pandas", "numpy", "shapely", "yaml", "httpx"):
        try:
            __import__(module)
            checks.append(_check(f"Dependency: {module}", True, "installed"))
        except ImportError as exc:
            checks.append(_check(f"Dependency: {module}", False, str(exc)))

    for optional, purpose in (("cv2", "image evidence redaction"), ("geopandas", "large-scale spatial analysis")):
        try:
            __import__(optional)
            checks.append(_check(f"Optional: {optional}", True, f"installed ({purpose})"))
        except ImportError:
            checks.append(
                _check(
                    f"Optional: {optional}",
                    False,
                    f"not installed - {purpose} is unavailable, everything else works",
                    warning=True,
                )
            )

    failures = [c for c in checks if c["status"] == "FAIL"]
    return {
        "checks": checks,
        "summary": {
            "pass": sum(1 for c in checks if c["status"] == "PASS"),
            "warning": sum(1 for c in checks if c["status"] == "WARNING"),
            "fail": len(failures),
            "ready": not failures,
        },
        "gpu": gpu,
        "platform": {
            "python": sys.version,
            "executable": sys.executable,
            "project_root": str(PROJECT_ROOT),
        },
    }


@router.get("/system/health")
def system_health(session: DbSession) -> dict[str, Any]:
    """Live resource usage for the System Health page."""
    from backend.workers.runner import get_run_manager

    settings = get_settings()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(PROJECT_ROOT))
    process = psutil.Process()
    gpu = detect_gpu()

    database_ok = True
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        database_ok = False

    manager = get_run_manager()
    active = [run_id for run_id in list(manager._controllers) if manager.is_active(run_id)]  # noqa: SLF001

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cpu_cores": psutil.cpu_count(),
        "ram": {
            "total_gb": round(memory.total / 1e9, 2),
            "used_gb": round(memory.used / 1e9, 2),
            "percent": memory.percent,
        },
        "process": {
            "rss_mb": round(process.memory_info().rss / 1e6, 1),
            "threads": process.num_threads(),
        },
        "disk": {
            "total_gb": round(disk.total / 1e9, 2),
            "free_gb": round(disk.free / 1e9, 2),
            "percent": disk.percent,
        },
        "gpu": gpu,
        "database": {"ok": database_ok, "url_scheme": settings.database_url.split(":")[0]},
        "workers": {"active_runs": active, "resumable_runs": manager.resumable_runs()},
        "compute_device": settings.compute_device,
    }
