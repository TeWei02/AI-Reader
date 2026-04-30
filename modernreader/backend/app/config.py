"""
ModernReader Configuration

All tuneable values live here.  No external config file is required at
runtime — change these constants directly before building your demo.
"""

import os

# ── Server ──────────────────────────────────────────────────────────────
HOST = os.getenv("MR_HOST", "0.0.0.0")
PORT = int(os.getenv("MR_PORT", "8765"))

CORS_ORIGINS = [
    "http://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5500",   # VS Code Live Server
    "null",                     # file:// origin
]

# ── WebSocket broadcast ──────────────────────────────────────────────────
# Maximum number of signal payloads kept in the in-memory ring buffer.
SIGNAL_BUFFER_SIZE = 200

# How often (seconds) the server pushes a heartbeat "ping" to idle WS clients.
WS_HEARTBEAT_INTERVAL = 5.0

# ── Inference (state classifier) ────────────────────────────────────────
# Window: last N audio signals are averaged before classifying.
INFERENCE_AUDIO_WINDOW = 5
INFERENCE_ENV_WINDOW   = 3

# Rule thresholds – tweak these to match your acoustic environment.
AUDIO_CALM_RMS_MAX       = 0.08
AUDIO_CALM_ZCR_MAX       = 0.08
AUDIO_CURIOUS_ZCR_MIN    = 0.12
AUDIO_ACTIVE_RMS_MIN     = 0.15
AUDIO_ALERT_CENTROID_MIN = 2500.0
AUDIO_CHAOTIC_RMS_MIN    = 0.25
AUDIO_CHAOTIC_ZCR_MIN    = 0.20

# Environment thresholds
ENV_HOT_TEMP             = 30.0    # °C — contributes to ACTIVE
ENV_DARK_LUX_MAX         = 50.0    # lux — contributes to CALM
ENV_BRIGHT_LUX_MIN       = 500.0   # lux — contributes to CURIOUS

# ── Mapping layer ────────────────────────────────────────────────────────
# Path to the YAML mapping config (relative to project root).
MAPPING_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "configs", "mapping.yaml"
)
