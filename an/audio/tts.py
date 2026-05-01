"""TTS provider protocol + supporting dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable


@dataclass(slots=True)
class VoiceMeta:
    """Metadata for a TTS voice as exposed by a provider."""

    voice_id: str
    name: str
    provider: str
    language: str = "en"
    gender: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AudioClip:
    """A rendered audio clip, on disk or in memory."""

    path: Path | None = None
    bytes_: bytes | None = None
    duration: float = 0.0
    sample_rate: int = 44100
    channels: int = 1
    voice_id: str | None = None
    transcript: str | None = None


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech provider."""

    name: str

    def synthesize(self, text: str, voice_id: str, **kw: Any) -> AudioClip:
        """Render ``text`` in ``voice_id``'s voice. Returns an AudioClip."""

    def list_voices(self) -> Iterable[VoiceMeta]:
        """Return all voices the provider exposes."""
