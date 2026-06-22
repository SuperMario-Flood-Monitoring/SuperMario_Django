from __future__ import annotations

import asyncio
from typing import Any

from channels.layers import get_channel_layer

from swmm_engine.interface import create_engine_session
from swmm_engine.llm_dispatcher import schedule_llm_analysis_dispatch


GROUP_NAME = "simulation"

engine = create_engine_session()
websocket_clients = 0
_broadcast_task: asyncio.Task[None] | None = None
_last_broadcast_key: tuple[int, str | None] | None = None


def status_payload() -> dict[str, Any]:
    payload = engine.status_payload()
    payload["websocketClients"] = websocket_clients
    return payload


async def broadcast(payload: dict[str, Any]) -> None:
    schedule_llm_analysis_dispatch(payload)
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        GROUP_NAME,
        {
            "type": "swmm.message",
            "payload": payload,
        },
    )


async def ensure_broadcast_loop() -> None:
    global _broadcast_task
    if _broadcast_task is None or _broadcast_task.done():
        _broadcast_task = asyncio.create_task(_broadcast_loop())


async def _broadcast_loop() -> None:
    global _last_broadcast_key
    while True:
        snapshot = engine.latest_snapshot()
        if snapshot:
            key = (
                int(snapshot.get("stepIndex") or 0),
                str(snapshot.get("type") or ""),
            )
            if key != _last_broadcast_key:
                await broadcast(snapshot)
                _last_broadcast_key = key

        status = engine.status_payload()
        if not status.get("hasSession"):
            return

        await asyncio.sleep(0.25)
