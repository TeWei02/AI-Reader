"""
Signal Gateway — REST endpoint

POST /api/signals
    Any signal node posts its payload here.
    The payload is validated, pushed to the inference engine, and
    broadcast to all connected WebSocket clients on /ws/signals.

GET /api/signals/recent
    Returns the last N raw signal payloads from the ring buffer.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.signal import SignalPayload, StateSnapshot
from app.services.inference import get_engine
from app.services.mapping import state_to_tangible
from app import config as cfg

router = APIRouter(prefix="/api/signals", tags=["signals"])

# ── In-memory ring buffer ────────────────────────────────────────────────────
_signal_buffer: Deque[dict] = deque(maxlen=cfg.SIGNAL_BUFFER_SIZE)


def _get_ws_manager(request: Request):
    """Retrieve the WebSocket manager stored on app.state by main.py."""
    return request.app.state.ws_manager


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("", summary="Ingest a signal from any node")
async def ingest_signal(payload: SignalPayload, request: Request):
    """
    Receive a signal payload, run inference, broadcast results.

    Returns the updated StateSnapshot + TangiblePayload.
    """
    # 1. Store raw signal
    raw = payload.model_dump()
    raw["_received_at"] = time.time()
    _signal_buffer.append(raw)

    # 2. Push to inference engine
    engine  = get_engine()
    snapshot: StateSnapshot = engine.push(payload)

    # 3. Build tangible payload
    tangible = state_to_tangible(snapshot)

    # 4. Broadcast over WebSocket
    ws_manager = _get_ws_manager(request)
    broadcast_msg = {
        "type"     : "state_update",
        "state"    : snapshot.model_dump(),
        "tangible" : tangible.model_dump(),
    }
    await ws_manager.broadcast(broadcast_msg)

    return {
        "ok"       : True,
        "state"    : snapshot.model_dump(),
        "tangible" : tangible.model_dump(),
    }


@router.get("/recent", summary="Last N raw signal payloads")
async def get_recent_signals(limit: int = 20):
    """Return the most recent signal payloads (up to 200)."""
    limit = min(limit, cfg.SIGNAL_BUFFER_SIZE)
    signals = list(_signal_buffer)[-limit:]
    return {"signals": signals, "count": len(signals)}
