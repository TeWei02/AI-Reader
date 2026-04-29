"""
Signal Node Models

Pydantic schemas for all signal types flowing through the ModernReader gateway.

Every node — audio, environment, vision, human input — serialises its output
into one of these models before POSTing to /api/signals.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from enum import Enum


# ──────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────

class SourceType(str, Enum):
    AUDIO       = "audio"
    ENVIRONMENT = "environment"
    VISION      = "vision"
    HUMAN       = "human"
    EXTERNAL    = "external"


class StateLabel(str, Enum):
    CALM    = "calm"
    CURIOUS = "curious"
    ACTIVE  = "active"
    ALERT   = "alert"
    CHAOTIC = "chaotic"
    UNKNOWN = "unknown"


# ──────────────────────────────────────────────
# Audio features (librosa / ESP32 FFT output)
# ──────────────────────────────────────────────

class AudioFeatures(BaseModel):
    rms:       float = Field(0.0, ge=0.0, description="Root Mean Square energy")
    zcr:       float = Field(0.0, ge=0.0, description="Zero-Crossing Rate")
    centroid:  float = Field(0.0, ge=0.0, description="Spectral centroid (Hz)")
    bandwidth: float = Field(0.0, ge=0.0, description="Spectral bandwidth (Hz)")
    rolloff:   float = Field(0.0, ge=0.0, description="Spectral rolloff (Hz)")
    tempo:     Optional[float] = Field(None, description="Estimated BPM")


# ──────────────────────────────────────────────
# Environment features (DHT22, BH1750, …)
# ──────────────────────────────────────────────

class EnvironmentFeatures(BaseModel):
    temperature:  Optional[float] = Field(None, description="°C")
    humidity:     Optional[float] = Field(None, ge=0.0, le=100.0, description="%")
    light_lux:    Optional[float] = Field(None, ge=0.0, description="lux")
    pressure_hpa: Optional[float] = Field(None, description="hPa")
    soil_moisture: Optional[float] = Field(None, ge=0.0, le=1.0, description="0–1 normalised")


# ──────────────────────────────────────────────
# Generic signal payload (posted by any node)
# ──────────────────────────────────────────────

class SignalPayload(BaseModel):
    node_id:     str = Field(..., description="Unique node identifier, e.g. 'audio-node-01'")
    source_type: SourceType
    timestamp:   float = Field(..., description="Unix epoch seconds (float)")
    features:    Dict[str, Any] = Field(
        default_factory=dict,
        description="Flat or nested feature dict.  Use AudioFeatures / EnvironmentFeatures helpers."
    )
    raw_value:   Optional[Any] = Field(None, description="Optional raw sensor value")


# ──────────────────────────────────────────────
# Inferred state (produced by Meaning Engine)
# ──────────────────────────────────────────────

class StateSnapshot(BaseModel):
    state:      StateLabel
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    timestamp:  float
    source_nodes: list[str] = Field(default_factory=list)
    debug:      Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────
# Tangible output (sent to ESP32 Output Node)
# ──────────────────────────────────────────────

class TangiblePayload(BaseModel):
    state:     StateLabel
    led:       list[int]   = Field([0, 0, 0], description="[R, G, B] 0–255")
    vibration: int         = Field(0, ge=0, le=255, description="PWM 0–255")
    servo:     int         = Field(90, ge=0, le=180, description="Angle 0–180°")
    label:     str         = ""
    timestamp: float       = 0.0
