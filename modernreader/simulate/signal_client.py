"""
ModernReader — Simulated Signal Client
════════════════════════════════════════════════════════════════════════════════

Two modes:

  1. SYNTHETIC (default)
     Generates synthetic audio features that cycle through all 5 states,
     optionally with gaussian noise.  No microphone required.

  2. MICROPHONE  (--mic flag)
     Captures live audio from the default input device, computes real features
     using librosa / numpy, and posts them to the gateway.

  3. ENVIRONMENT (--env flag, runs in addition to audio)
     Posts simulated temperature / light readings every 5 seconds.

Usage
──────
  cd modernreader
  python simulate/signal_client.py                          # synthetic audio
  python simulate/signal_client.py --mic                    # live microphone
  python simulate/signal_client.py --mic --env              # mic + environment
  python simulate/signal_client.py --state alert            # inject single state
  python simulate/signal_client.py --help

Dependencies
─────────────
  pip install requests numpy
  pip install sounddevice librosa   # only needed for --mic mode

════════════════════════════════════════════════════════════════════════════════
"""

import argparse
import json
import math
import random
import sys
import time
from typing import Optional

import requests

# ── Optional imports ─────────────────────────────────────────────────────────

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import sounddevice as sd
    import librosa
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SERVER = "http://localhost:8765"
AUDIO_NODE_ID  = "sim-audio-01"
ENV_NODE_ID    = "sim-env-01"

# Audio capture settings (used only in --mic mode)
SAMPLE_RATE    = 22050
HOP_LENGTH     = 512
CHUNK_DURATION = 0.5     # seconds per analysis frame
CHUNK_SAMPLES  = int(SAMPLE_RATE * CHUNK_DURATION)

# Synthetic cycle speed (seconds per state)
STATE_CYCLE_INTERVAL = 8.0
SYNTHETIC_POST_RATE  = 0.5   # seconds between POST requests in synthetic mode

# Environment simulation post interval
ENV_POST_INTERVAL = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic feature generators
# ─────────────────────────────────────────────────────────────────────────────

# Each state: (rms_base, zcr_base, centroid_base, bandwidth_base)
_STATE_FEATURES = {
    "calm"    : (0.04, 0.05,  800,  400),
    "curious" : (0.07, 0.14, 1200,  600),
    "active"  : (0.20, 0.11, 1800,  900),
    "alert"   : (0.18, 0.10, 3200, 1200),
    "chaotic" : (0.30, 0.25, 4200, 2000),
}

_STATE_ORDER = ["calm", "curious", "active", "alert", "chaotic"]


def _noise(v: float, scale: float = 0.05) -> float:
    return max(0.0, v + random.gauss(0, abs(v) * scale + 1e-6))


def synthetic_features(state: str) -> dict:
    """Return a noisy feature dict for the given state."""
    rms, zcr, centroid, bw = _STATE_FEATURES.get(state, _STATE_FEATURES["calm"])
    return {
        "rms"      : round(_noise(rms,      0.10), 4),
        "zcr"      : round(_noise(zcr,      0.10), 4),
        "centroid" : round(_noise(centroid, 0.08), 1),
        "bandwidth": round(_noise(bw,       0.08), 1),
        "rolloff"  : round(_noise(centroid * 1.6, 0.08), 1),
    }


def synthetic_env_features() -> dict:
    """Simulated environment readings with slight drift."""
    base_temp = 24.0 + math.sin(time.time() / 30) * 4.0
    base_lux  = 200  + math.sin(time.time() / 20) * 150
    return {
        "temperature" : round(base_temp + random.gauss(0, 0.3), 2),
        "humidity"    : round(60 + random.gauss(0, 2), 1),
        "light_lux"   : round(max(0, base_lux + random.gauss(0, 10)), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Audio feature extraction (librosa)
# ─────────────────────────────────────────────────────────────────────────────

def extract_audio_features(audio: "np.ndarray", sr: int) -> dict:
    """Extract signal features from a numpy audio array."""
    rms      = float(librosa.feature.rms(y=audio).mean())
    zcr      = float(librosa.feature.zero_crossing_rate(y=audio).mean())
    centroid = float(librosa.feature.spectral_centroid(y=audio, sr=sr).mean())
    bw       = float(librosa.feature.spectral_bandwidth(y=audio, sr=sr).mean())
    rolloff  = float(librosa.feature.spectral_rolloff(y=audio, sr=sr).mean())
    return {
        "rms"      : round(rms,      5),
        "zcr"      : round(zcr,      5),
        "centroid" : round(centroid, 1),
        "bandwidth": round(bw,       1),
        "rolloff"  : round(rolloff,  1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client
# ─────────────────────────────────────────────────────────────────────────────

def post_signal(server: str, node_id: str, source_type: str, features: dict) -> bool:
    payload = {
        "node_id"    : node_id,
        "source_type": source_type,
        "timestamp"  : time.time(),
        "features"   : features,
    }
    try:
        r = requests.post(
            f"{server}/api/signals",
            json=payload,
            timeout=3.0,
        )
        r.raise_for_status()
        data = r.json()
        state    = data.get("state", {}).get("state", "?")
        tangible = data.get("tangible", {})
        print(
            f"  → {source_type.upper():11s}  state={state:<8s}"
            f"  led={tangible.get('led',[])}"
            f"  vib={tangible.get('vibration','?')}"
            f"  servo={tangible.get('servo','?')}°"
        )
        return True
    except requests.RequestException as e:
        print(f"  [ERR] POST failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Runner modes
# ─────────────────────────────────────────────────────────────────────────────

def run_synthetic(server: str, fixed_state: Optional[str] = None) -> None:
    print(f"[SIM] Synthetic mode → {server}")
    print("      States cycle every 8 s.  Press Ctrl+C to stop.\n")

    cycle_start = time.time()
    idx = 0

    while True:
        if fixed_state:
            state = fixed_state
        else:
            elapsed = time.time() - cycle_start
            idx = int(elapsed / STATE_CYCLE_INTERVAL) % len(_STATE_ORDER)
            state = _STATE_ORDER[idx]

        features = synthetic_features(state)
        print(f"[{time.strftime('%H:%M:%S')}] injecting {state.upper()}", end="")
        post_signal(server, AUDIO_NODE_ID, "audio", features)

        if fixed_state:
            break   # single shot

        time.sleep(SYNTHETIC_POST_RATE)


def run_microphone(server: str, with_env: bool = False) -> None:
    if not HAS_AUDIO:
        print("[ERR] sounddevice / librosa not installed.")
        print("      pip install sounddevice librosa")
        sys.exit(1)

    print(f"[SIM] Microphone mode → {server}")
    print(f"      Chunk: {CHUNK_DURATION}s  SR: {SAMPLE_RATE}Hz  Press Ctrl+C to stop.\n")

    env_last = 0.0

    def audio_callback(indata, frames, _time, status):
        nonlocal env_last
        if status:
            print(f"  [AUDIO STATUS] {status}")
        audio_mono = indata[:, 0].copy()
        features = extract_audio_features(audio_mono, SAMPLE_RATE)
        print(f"[{time.strftime('%H:%M:%S')}] mic", end="")
        post_signal(server, AUDIO_NODE_ID, "audio", features)

        if with_env and (time.time() - env_last) >= ENV_POST_INTERVAL:
            env_last = time.time()
            env_feats = synthetic_env_features()
            print(f"[{time.strftime('%H:%M:%S')}] env", end="")
            post_signal(server, ENV_NODE_ID, "environment", env_feats)

    with sd.InputStream(
        samplerate = SAMPLE_RATE,
        channels   = 1,
        blocksize  = CHUNK_SAMPLES,
        callback   = audio_callback,
    ):
        while True:
            time.sleep(0.1)


def run_env_only(server: str) -> None:
    print(f"[SIM] Environment-only mode → {server}  Press Ctrl+C to stop.\n")
    while True:
        features = synthetic_env_features()
        print(f"[{time.strftime('%H:%M:%S')}] env", end="")
        post_signal(server, ENV_NODE_ID, "environment", features)
        time.sleep(ENV_POST_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ModernReader simulated signal client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server", default=DEFAULT_SERVER,
        help=f"Gateway URL (default: {DEFAULT_SERVER})"
    )
    parser.add_argument(
        "--mic", action="store_true",
        help="Capture live microphone audio (requires sounddevice + librosa)"
    )
    parser.add_argument(
        "--env", action="store_true",
        help="Also post simulated environment readings"
    )
    parser.add_argument(
        "--state", choices=list(_STATE_FEATURES.keys()),
        help="Inject a single state and exit (synthetic one-shot)"
    )
    args = parser.parse_args()

    # One-shot state injection
    if args.state:
        run_synthetic(args.server, fixed_state=args.state)
        return

    # Env-only
    if args.env and not args.mic:
        run_env_only(args.server)
        return

    # Microphone
    if args.mic:
        run_microphone(args.server, with_env=args.env)
        return

    # Default: synthetic
    run_synthetic(args.server)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SIM] Stopped.")
