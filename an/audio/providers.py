"""Provider factory: name → concrete TTS/LipSync provider instance.

Used by the CLI and orchestrator to map ``--tts elevenlabs`` (and similar)
strings into instantiated providers without callers having to import the
specific classes.

>>> from an.audio.providers import make_tts, make_lipsync
>>> make_tts("offline").name
'offline'
>>> make_lipsync("offline").name
'offline'
"""

from __future__ import annotations

from typing import Callable

from an.audio.elevenlabs_tts import ElevenLabsTTS
from an.audio.lipsync import LipSyncProvider
from an.audio.mac_say_tts import MacSayTTS
from an.audio.offline_lipsync import OfflineLipSync
from an.audio.offline_tts import OfflineTTS
from an.audio.rhubarb_lipsync import RhubarbLipSync
from an.audio.tts import TTSProvider
from an.audio.whisper_lipsync import WhisperLipSync


TTS_FACTORIES: dict[str, Callable[[], TTSProvider]] = {
    "offline": lambda: OfflineTTS(),
    "elevenlabs": lambda: ElevenLabsTTS(),
    "mac_say": lambda: MacSayTTS(),
}

#: The language a provider aligns for when the caller says nothing. Only
#: Rhubarb reads it today (its recognizer follows the language, an#96).
DEFAULT_LANGUAGE: str = "en"

LIPSYNC_FACTORIES: dict[str, Callable[..., LipSyncProvider]] = {
    "offline": lambda **_: OfflineLipSync(),
    "rhubarb": lambda *, language=DEFAULT_LANGUAGE: RhubarbLipSync(language=language),
    "whisper": lambda **_: WhisperLipSync(),
}


def make_tts(name: str) -> TTSProvider:
    """Instantiate a TTS provider by name.

    Raises ``ValueError`` for unknown names with a list of known options.
    """
    try:
        return TTS_FACTORIES[name]()
    except KeyError:
        raise ValueError(
            f"unknown TTS provider {name!r}; known: {sorted(TTS_FACTORIES)}"
        ) from None


def make_lipsync(name: str, *, language: str = DEFAULT_LANGUAGE) -> LipSyncProvider:
    """Instantiate a LipSync provider by name.

    ``language`` (BCP-47) reaches providers that select behaviour by language —
    Rhubarb's recognizer today; a future aligner's weight allowlist.
    """
    try:
        return LIPSYNC_FACTORIES[name](language=language)
    except KeyError:
        raise ValueError(
            f"unknown LipSync provider {name!r}; known: {sorted(LIPSYNC_FACTORIES)}"
        ) from None


def known_tts_names() -> list[str]:
    """Return the registered TTS provider names."""
    return sorted(TTS_FACTORIES)


def known_lipsync_names() -> list[str]:
    """Return the registered LipSync provider names."""
    return sorted(LIPSYNC_FACTORIES)
