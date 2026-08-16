"""Run checkpointing.

A cancelled, paused or interrupted run saves enough state to resume without
re-processing what it already did:

    last_processed_event, page_cursor, processed_count, completed_stages,
    processed_event_ids

Checkpoints live both on the run row (so the API can show them) and as JSON on
disk (so a resume survives a database that was restored from an earlier point).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.settings import get_settings

log = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    run_id: str
    last_processed_event: str | None = None
    page_cursor: str | None = None
    processed_count: int = 0
    discovered_count: int = 0
    completed_stages: list[str] = field(default_factory=list)
    processed_event_ids: list[str] = field(default_factory=list)
    counters: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class CheckpointStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or get_settings().checkpoint_dir
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"

    def save(self, checkpoint: Checkpoint) -> Path:
        from datetime import datetime, timezone

        checkpoint.updated_at = datetime.now(timezone.utc).isoformat()
        path = self.path_for(checkpoint.run_id)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(checkpoint.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
            temporary.replace(path)  # atomic: a crash mid-write cannot corrupt the checkpoint
        except OSError as exc:
            log.warning("Could not persist checkpoint for %s: %s", checkpoint.run_id, exc)
        return path

    def load(self, run_id: str) -> Checkpoint | None:
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            return Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("Checkpoint for %s is unreadable and will be ignored: %s", run_id, exc)
            return None

    def discard(self, run_id: str) -> None:
        path = self.path_for(run_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            log.warning("Could not remove checkpoint for %s: %s", run_id, exc)

    def list_resumable(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.json"))
