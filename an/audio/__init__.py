"""Audio pipeline — TTS and lip-sync providers + orchestration.

Phase 3 ships:

- Protocols: ``TTSProvider``, ``LipSyncProvider`` (Phase 1).
- Default offline providers: ``OfflineTTS`` (silent WAV), ``OfflineLipSync``
  (deterministic char-to-viseme).
- Real providers: ``ElevenLabsTTS`` (needs ``ELEVEN_API_KEY``),
  ``RhubarbLipSync`` (needs ``rhubarb`` binary).
- Orchestration: ``produce_audio_for_dialogue`` /
  ``produce_audio_for_scene`` walk a SceneIR, synthesize, persist to mall,
  stamp viseme tracks back onto the IR.

The defaults are intentionally offline so the entire `an` pipeline works
without external services.
"""

from an.audio.tts import TTSProvider, AudioClip, VoiceMeta
from an.audio.lipsync import (
    LipSyncProvider,
    Viseme,
    VisemeTrack,
    WordTiming,
    WordTimingProvider,
    word_timings_to_visemes,
)
from an.audio.offline_tts import OfflineTTS
from an.audio.offline_lipsync import OfflineLipSync
from an.audio.elevenlabs_tts import ElevenLabsTTS
from an.audio.mac_say_tts import MacSayTTS
from an.audio.rhubarb_lipsync import RhubarbLipSync
from an.audio.whisper_lipsync import WhisperLipSync
from an.audio.injectable_lipsync import StaticWordTimings, WordTimingsLipSync
from an.audio.pipeline import (
    default_tts,
    default_lipsync,
    produce_audio_for_dialogue,
    produce_audio_for_scene,
)
from an.audio.providers import (
    make_tts,
    make_lipsync,
    known_tts_names,
    known_lipsync_names,
)

__all__ = [
    "TTSProvider",
    "AudioClip",
    "VoiceMeta",
    "LipSyncProvider",
    "Viseme",
    "VisemeTrack",
    "WordTiming",
    "WordTimingProvider",
    "word_timings_to_visemes",
    "OfflineTTS",
    "OfflineLipSync",
    "ElevenLabsTTS",
    "MacSayTTS",
    "RhubarbLipSync",
    "WhisperLipSync",
    "StaticWordTimings",
    "WordTimingsLipSync",
    "default_tts",
    "default_lipsync",
    "produce_audio_for_dialogue",
    "produce_audio_for_scene",
    "make_tts",
    "make_lipsync",
    "known_tts_names",
    "known_lipsync_names",
]
