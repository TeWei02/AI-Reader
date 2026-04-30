"""
Mapping Layer — State → Tangible Payload

Reads the mapping.yaml config and converts a StateSnapshot into a
TangiblePayload that can be pushed to the ESP32 Output Node via WebSocket.

Design notes
------------
* The YAML file is loaded once at module import; call reload_config() if you
  hot-swap the file during development.
* This layer is intentionally decoupled from inference — you can add new
  output modalities (AR scene, podcast prompt, haptic pattern) by extending
  TangiblePayload and adding keys here.
"""

from __future__ import annotations

import os
import time
from typing import Dict, Any

try:
    import yaml  # PyYAML
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from app.models.signal import StateLabel, StateSnapshot, TangiblePayload
from app import config as cfg


# ─────────────────────────────────────────────────────────────────────────────
# Built-in fallback table  (used when mapping.yaml is not found or PyYAML
# is not installed)
# ─────────────────────────────────────────────────────────────────────────────

_FALLBACK: Dict[str, Dict[str, Any]] = {
    "calm"    : {"led": [0, 60, 80],    "vibration": 0,   "servo": 80,  "label": "calm"},
    "curious" : {"led": [0, 120, 200],  "vibration": 30,  "servo": 100, "label": "curious"},
    "active"  : {"led": [0, 200, 50],   "vibration": 80,  "servo": 110, "label": "active"},
    "alert"   : {"led": [255, 40, 0],   "vibration": 180, "servo": 150, "label": "alert"},
    "chaotic" : {"led": [180, 0, 255],  "vibration": 200, "servo": 40,  "label": "chaotic"},
    "unknown" : {"led": [20, 20, 20],   "vibration": 0,   "servo": 90,  "label": "unknown"},
}


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

_mapping_table: Dict[str, Dict[str, Any]] = {}


def _load_yaml() -> Dict[str, Dict[str, Any]]:
    path = cfg.MAPPING_CONFIG_PATH
    if not _HAS_YAML:
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data.get("states", {})
    except Exception:
        return {}


def reload_config() -> None:
    """Hot-reload mapping.yaml (useful during development)."""
    global _mapping_table
    loaded = _load_yaml()
    _mapping_table = loaded if loaded else _FALLBACK.copy()


# Load on import
reload_config()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def state_to_tangible(snapshot: StateSnapshot) -> TangiblePayload:
    """
    Convert a StateSnapshot into a TangiblePayload.

    Returns
    -------
    TangiblePayload
        Ready to serialise and push over WebSocket to the output node.
    """
    key = snapshot.state.value
    row = _mapping_table.get(key) or _FALLBACK.get(key) or _FALLBACK["unknown"]

    return TangiblePayload(
        state     = snapshot.state,
        led       = row.get("led",       [20, 20, 20]),
        vibration = row.get("vibration", 0),
        servo     = row.get("servo",     90),
        label     = row.get("label",     key),
        timestamp = snapshot.timestamp or time.time(),
    )


def get_full_mapping() -> Dict[str, Dict[str, Any]]:
    """Return the entire mapping table (for API inspection / dashboard)."""
    return dict(_mapping_table or _FALLBACK)
