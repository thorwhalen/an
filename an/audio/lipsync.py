"""Lip-sync provider protocol + viseme dataclasses.

Viseme conventions are renderer-agnostic in the IR; concrete providers in
later phases (Rhubarb, Azure, MFA) emit their own letter/number encodings,
which the cutout adapter then maps to mouth-slot attachments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from an.audio.tts import AudioClip


@dataclass(slots=True, frozen=True)
class Viseme:
    """A single mouth-shape keyframe."""

    time: float  # seconds from start of clip
    code: str  # provider-specific code (Rhubarb A-X, Azure name, etc.)
    intensity: float = 1.0


@dataclass(slots=True)
class VisemeTrack:
    """Aligned viseme sequence produced by a LipSyncProvider."""

    visemes: list[Viseme] = field(default_factory=list)
    convention: str = "rhubarb"  # "rhubarb", "azure22", "mpeg4", etc.
    duration: float = 0.0


@runtime_checkable
class LipSyncProvider(Protocol):
    """Audio + transcript → aligned viseme track."""

    name: str
    convention: str  # which viseme convention this provider emits

    def align(self, audio: AudioClip, transcript: str) -> VisemeTrack:
        """Produce a viseme track for ``audio`` given its ``transcript``."""
