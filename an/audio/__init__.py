"""Audio pipeline — TTS and lip-sync protocols (concrete impls land in P3)."""

from an.audio.tts import TTSProvider, AudioClip, VoiceMeta
from an.audio.lipsync import LipSyncProvider, Viseme, VisemeTrack

__all__ = [
    "TTSProvider",
    "AudioClip",
    "VoiceMeta",
    "LipSyncProvider",
    "Viseme",
    "VisemeTrack",
]
