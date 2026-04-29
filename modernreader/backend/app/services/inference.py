"""
Meaning Engine — State Inference (v1: rule-based)

Pipeline
--------
1. Receive a SignalPayload from the gateway.
2. Push it into a sliding window per source_type.
3. Average the window to get a stable feature vector.
4. Apply deterministic rules → StateLabel.
5. Optionally fuse audio + environment into a combined state.

This module is intentionally self-contained and has no external
dependencies — numpy is used only where available.

Upgrade path
------------
v2: Replace _classify_audio() / _classify_env() with scikit-learn models.
v3: Swap with a CNN spectrogram classifier or multimodal encoder.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional

from app.models.signal import (
    SignalPayload,
    StateLabel,
    StateSnapshot,
    SourceType,
    AudioFeatures,
    EnvironmentFeatures,
)
from app import config as cfg


# ─────────────────────────────────────────────────────────────────────────────
# Sliding-window accumulators
# ─────────────────────────────────────────────────────────────────────────────

class _AudioWindow:
    """Keep the last N audio feature dicts and return their mean."""

    def __init__(self, size: int = cfg.INFERENCE_AUDIO_WINDOW) -> None:
        self._buf: Deque[dict] = deque(maxlen=size)

    def push(self, features: dict) -> None:
        self._buf.append(features)

    def mean(self) -> dict:
        if not self._buf:
            return {}
        keys = self._buf[0].keys()
        result = {}
        for k in keys:
            vals = [f[k] for f in self._buf if isinstance(f.get(k), (int, float))]
            result[k] = sum(vals) / len(vals) if vals else 0.0
        return result

    def __len__(self) -> int:
        return len(self._buf)


class _EnvWindow:
    """Keep the last N environment feature dicts and return their mean."""

    def __init__(self, size: int = cfg.INFERENCE_ENV_WINDOW) -> None:
        self._buf: Deque[dict] = deque(maxlen=size)

    def push(self, features: dict) -> None:
        self._buf.append(features)

    def mean(self) -> dict:
        if not self._buf:
            return {}
        keys = self._buf[0].keys()
        result = {}
        for k in keys:
            vals = [f[k] for f in self._buf if isinstance(f.get(k), (int, float))]
            result[k] = sum(vals) / len(vals) if vals else 0.0
        return result

    def __len__(self) -> int:
        return len(self._buf)


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based classifiers
# ─────────────────────────────────────────────────────────────────────────────

def _classify_audio(f: dict) -> Optional[StateLabel]:
    """
    Map averaged audio features → StateLabel.

    Feature priority (highest wins):
      chaotic  → very high RMS + high ZCR
      alert    → high spectral centroid
      active   → high RMS
      curious  → medium ZCR (texture without energy)
      calm     → low RMS + low ZCR
    """
    rms      = f.get("rms", 0.0)
    zcr      = f.get("zcr", 0.0)
    centroid = f.get("centroid", 0.0)

    if rms >= cfg.AUDIO_CHAOTIC_RMS_MIN and zcr >= cfg.AUDIO_CHAOTIC_ZCR_MIN:
        return StateLabel.CHAOTIC
    if centroid >= cfg.AUDIO_ALERT_CENTROID_MIN:
        return StateLabel.ALERT
    if rms >= cfg.AUDIO_ACTIVE_RMS_MIN:
        return StateLabel.ACTIVE
    if zcr >= cfg.AUDIO_CURIOUS_ZCR_MIN:
        return StateLabel.CURIOUS
    if rms <= cfg.AUDIO_CALM_RMS_MAX and zcr <= cfg.AUDIO_CALM_ZCR_MAX:
        return StateLabel.CALM
    return None   # ambiguous — let fusion decide


def _classify_env(f: dict) -> Optional[StateLabel]:
    """
    Map averaged environment features → StateLabel modifier.

    This is intentionally simple and acts as a *context modifier*,
    not a primary classifier.
    """
    temp = f.get("temperature")
    lux  = f.get("light_lux")

    if temp is not None and temp >= cfg.ENV_HOT_TEMP:
        return StateLabel.ACTIVE
    if lux is not None and lux <= cfg.ENV_DARK_LUX_MAX:
        return StateLabel.CALM
    if lux is not None and lux >= cfg.ENV_BRIGHT_LUX_MIN:
        return StateLabel.CURIOUS
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fusion layer
# ─────────────────────────────────────────────────────────────────────────────

_STATE_PRIORITY = {
    StateLabel.CHAOTIC : 5,
    StateLabel.ALERT   : 4,
    StateLabel.ACTIVE  : 3,
    StateLabel.CURIOUS : 2,
    StateLabel.CALM    : 1,
    StateLabel.UNKNOWN : 0,
}


def _fuse(audio_state: Optional[StateLabel], env_state: Optional[StateLabel]) -> StateLabel:
    """
    Combine audio and environment signals.

    Strategy: return whichever has higher priority; audio wins ties.
    Falls back to UNKNOWN only when both are None.
    """
    if audio_state is None and env_state is None:
        return StateLabel.UNKNOWN
    if audio_state is None:
        return env_state  # type: ignore[return-value]
    if env_state is None:
        return audio_state

    ap = _STATE_PRIORITY.get(audio_state, 0)
    ep = _STATE_PRIORITY.get(env_state, 0)
    return audio_state if ap >= ep else env_state


# ─────────────────────────────────────────────────────────────────────────────
# Public inference engine
# ─────────────────────────────────────────────────────────────────────────────

class InferenceEngine:
    """
    Stateful inference engine.

    Usage::

        engine = InferenceEngine()
        engine.push(signal_payload)
        snapshot = engine.latest_state()
    """

    def __init__(self) -> None:
        self._audio_win = _AudioWindow()
        self._env_win   = _EnvWindow()
        self._last_audio_nodes: list[str] = []
        self._last_env_nodes:   list[str] = []
        self._current: StateSnapshot = StateSnapshot(
            state=StateLabel.UNKNOWN,
            confidence=0.0,
            timestamp=time.time(),
        )

    # ── ingest ──────────────────────────────────────────────────────────

    def push(self, payload: SignalPayload) -> StateSnapshot:
        """Ingest a signal payload and return the updated state snapshot."""
        if payload.source_type == SourceType.AUDIO:
            self._audio_win.push(payload.features)
            self._last_audio_nodes = [payload.node_id]

        elif payload.source_type == SourceType.ENVIRONMENT:
            self._env_win.push(payload.features)
            self._last_env_nodes = [payload.node_id]

        # Other source types are accepted but not yet wired to classifiers.
        # They extend here in v2+.

        self._current = self._infer()
        return self._current

    # ── inference ───────────────────────────────────────────────────────

    def _infer(self) -> StateSnapshot:
        audio_mean  = self._audio_win.mean()
        env_mean    = self._env_win.mean()

        audio_state = _classify_audio(audio_mean) if audio_mean else None
        env_state   = _classify_env(env_mean)     if env_mean   else None

        fused = _fuse(audio_state, env_state)

        # Rough confidence: proportional to how full the windows are
        audio_fill = len(self._audio_win) / self._audio_win._buf.maxlen  # type: ignore[attr-defined]
        env_fill   = len(self._env_win)   / self._env_win._buf.maxlen    # type: ignore[attr-defined]
        confidence = (audio_fill + env_fill) / 2 if (audio_fill + env_fill) > 0 else 0.5

        return StateSnapshot(
            state        = fused,
            confidence   = round(confidence, 3),
            timestamp    = time.time(),
            source_nodes = self._last_audio_nodes + self._last_env_nodes,
            debug        = {
                "audio_mean"  : audio_mean,
                "env_mean"    : env_mean,
                "audio_state" : audio_state.value if audio_state else None,
                "env_state"   : env_state.value   if env_state   else None,
            },
        )

    # ── query ───────────────────────────────────────────────────────────

    def latest_state(self) -> StateSnapshot:
        return self._current

    def reset(self) -> None:
        self.__init__()


# Module-level singleton so routes can share one engine instance.
_engine: Optional[InferenceEngine] = None


def get_engine() -> InferenceEngine:
    global _engine
    if _engine is None:
        _engine = InferenceEngine()
    return _engine
