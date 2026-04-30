"""
ModernReader — Signal Gateway

Entry point.  Run with:

    cd modernreader/backend
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8765

Architecture
------------

    [Signal Nodes]
        ESP32 audio node  →  POST /api/signals
        ESP32 env node    →  POST /api/signals
        simulate/signal_client.py  →  POST /api/signals

    [Signal Gateway]  (this file)
        FastAPI + WebSocket hub
        ConnectionManager on app.state.ws_manager

    [Meaning Engine]
        app/services/inference.py  — sliding-window rule classifier

    [Mapping Layer]
        app/services/mapping.py  — state → LED/vibration/servo

    [Experience Output]
        WS /ws  →  ESP32 output node  →  LED / vibration / servo
        GET /api/state/latest  →  dashboard polling
        WS /ws  →  dashboard live updates
"""

import os
import sys

# Make "app" importable when launched from the backend/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config as cfg
from app.routes.signals import router as signals_router
from app.routes.events  import router as events_router, router_ws, ConnectionManager

app = FastAPI(
    title       = "ModernReader Signal Gateway",
    description = (
        "Signal → Meaning → Tangible Response.\n\n"
        "POST /api/signals  — ingest any sensor payload\n"
        "WS  /ws            — real-time state broadcast\n"
        "GET /api/state/latest — current state snapshot"
    ),
    version     = "1.0.0-mvp",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = cfg.CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── WebSocket manager (shared across routes) ──────────────────────────────────
app.state.ws_manager = ConnectionManager()

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(signals_router)
app.include_router(events_router)
app.include_router(router_ws)


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return {
        "name"   : "ModernReader Signal Gateway",
        "version": "1.0.0-mvp",
        "docs"   : "/docs",
        "ws"     : "ws://localhost:8765/ws",
        "post"   : "/api/signals",
        "state"  : "/api/state/latest",
    }
