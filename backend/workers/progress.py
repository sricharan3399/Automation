"""Thread-safe progress broadcasting to WebSocket subscribers.

Runs execute on worker threads; WebSocket handlers live on the asyncio loop.
This hub bridges the two: workers call :meth:`publish` from any thread, and the
message is handed to each subscriber's queue on the loop thread.

Subscriber queues are bounded. A client that stops draining loses its oldest
progress frames rather than growing memory without limit - progress frames are
snapshots, so dropping stale ones is always safe.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 64


class ProgressHub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._latest: dict[str, dict[str, Any]] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- subscription ----------------------------------------------------
    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers.setdefault(topic, set()).add(queue)
            latest = self._latest.get(topic)
        if latest is not None:
            # Give a new subscriber the current state immediately.
            queue.put_nowait(latest)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(topic)
            if subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(topic, None)

    # -- publishing ------------------------------------------------------
    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._latest[topic] = payload
            targets = list(self._subscribers.get(topic, ()))
            broadcast = list(self._subscribers.get("*", ()))

        queues = targets + broadcast
        if not queues:
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, queues, payload)
        except RuntimeError:  # loop shutting down
            pass

    @staticmethod
    def _deliver(queues: list[asyncio.Queue[dict[str, Any]]], payload: dict[str, Any]) -> None:
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()  # drop the oldest frame
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover - raced with another consumer
                pass

    def latest(self, topic: str) -> dict[str, Any] | None:
        with self._lock:
            return self._latest.get(topic)

    def clear(self, topic: str) -> None:
        with self._lock:
            self._latest.pop(topic, None)


_hub = ProgressHub()


def get_progress_hub() -> ProgressHub:
    return _hub
