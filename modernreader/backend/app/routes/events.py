"""
Event Gateway — WebSocket channels + state query

WS  /ws
    Bi-directional channel.  Any connected client (dashboard, output node,
    mobile app) receives every state_update broadcast.
    Clients may also send JSON frames; the server echoes them back with a
    type="echo" wrapper (reserved for future command messages).

GET /api/state/latest
    Synchronous snapshot of the current state + tangible payload.
    Useful for polling clients or initial page load.

GET /api/mapping
    Returns the full state→tangible mapping table (for dashboard config panel).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.services.inference import get_engine
from app.services.mapping import state_to_tangible, get_full_mapping
from app import config as cfg

router = APIRouter(tags=["events"])


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionManager:
    """
    Manages all active WebSocket connections.

    Stored on ``app.state.ws_manager`` so routes can reference it.
    """

    def __init__(self) -> None:
        self._active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to every connected client."""
        dead: list[WebSocket] = []
        for ws in list(self._active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._active)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

router_ws = APIRouter(tags=["websocket"])


@router_ws.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Main WebSocket channel.

    On connect: sends the current state immediately.
    Then: listens for client messages and echoes; server-push happens
          via ConnectionManager.broadcast() called from the signals route.
    A background heartbeat pings every N seconds to keep the connection alive.
    """
    manager: ConnectionManager = ws.app.state.ws_manager
    await manager.connect(ws)

    # Send current state on connect so the client is immediately synced.
    try:
        engine   = get_engine()
        snapshot = engine.latest_state()
        tangible = state_to_tangible(snapshot)
        await ws.send_json({
            "type"    : "connected",
            "state"   : snapshot.model_dump(),
            "tangible": tangible.model_dump(),
            "clients" : manager.count,
        })
    except Exception:
        pass

    # Background heartbeat
    async def _heartbeat():
        while True:
            await asyncio.sleep(cfg.WS_HEARTBEAT_INTERVAL)
            try:
                await ws.send_json({"type": "ping", "ts": time.time()})
            except Exception:
                break

    hb_task = asyncio.create_task(_heartbeat())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"raw": raw}
            # Echo back (reserved for future client→server commands)
            await ws.send_json({"type": "echo", "payload": msg})
    except WebSocketDisconnect:
        pass
    finally:
        hb_task.cancel()
        manager.disconnect(ws)


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/state/latest", summary="Current state snapshot")
async def get_latest_state():
    """
    Return the most recently inferred state and its tangible mapping.

    This is the polling fallback for clients that cannot use WebSocket.
    """
    engine   = get_engine()
    snapshot = engine.latest_state()
    tangible = state_to_tangible(snapshot)
    return {
        "state"   : snapshot.model_dump(),
        "tangible": tangible.model_dump(),
    }


@router.get("/api/mapping", summary="Full state→tangible mapping table")
async def get_mapping():
    """Return the loaded mapping config (useful for dashboard config panel)."""
    return get_full_mapping()


@router.get("/api/health", summary="Health check")
async def health():
    return {"status": "ok", "ts": time.time()}
