"""WebSocket endpoints for live run progress."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.workers.progress import get_progress_hub

log = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])

HEARTBEAT_SECONDS = 20.0


async def _stream(websocket: WebSocket, topic: str) -> None:
    hub = get_progress_hub()
    await websocket.accept()
    queue = hub.subscribe(topic)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # A heartbeat keeps intermediaries from closing an idle socket
                # during a long-running stage.
                await websocket.send_json({"type": "heartbeat", "topic": topic})
                continue
            await websocket.send_json({"type": "progress", "topic": topic, "data": payload})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport level
        log.debug("WebSocket stream for %s ended: %s", topic, exc)
    finally:
        hub.unsubscribe(topic, queue)


@router.websocket("/ws/runs/{run_id}")
async def run_progress(websocket: WebSocket, run_id: str) -> None:
    await _stream(websocket, run_id)


@router.websocket("/ws/runs")
async def all_run_progress(websocket: WebSocket) -> None:
    """Progress for every run, used by the Home and Automation Runs pages."""
    await _stream(websocket, "*")
