"""Lip-sync provider that consumes pre-computed word timings.

Useful when an upstream system already has authoritative word-level
timings and would otherwise force ``an`` to re-transcribe the same
audio. The canonical case is ``muvid``, where the lyric → audio
alignment store (``lacing``) is the SSOT and re-running
``WhisperLipSync`` on the audio produces a redundant (and possibly
divergent) word-timestamp set.

Two pieces:

- :class:`StaticWordTimings` — a :class:`WordTimingProvider` over a
  fixed list of ``(word, start, end)`` tuples.
- :class:`WordTimingsLipSync` — a :class:`LipSyncProvider` that reads
  from any :class:`WordTimingProvider` and runs the same
  word→viseme conversion as :class:`WhisperLipSync` (via
  :func:`word_timings_to_visemes`), so output is shape-compatible with
  the rest of the cutout pipeline.

Drop-in usage::

    from an.audio.injectable_lipsync import (
        StaticWordTimings, WordTimingsLipSync,
    )

    timings = [("hello", 0.5, 1.0), ("world", 1.2, 1.8)]
    lipsync = WordTimingsLipSync(StaticWordTimings(timings))
    track = lipsync.align(audio_clip, "hello world")
"""

from __future__ import annotations

from typing import Sequence

from an.audio.lipsync import (
    LipSyncProvider,
    VisemeTrack,
    WordTiming,
    WordTimingProvider,
    word_timings_to_visemes,
)
from an.audio.offline_lipsync import _CHAR_TO_VISEME, _REST_VISEME
from an.audio.tts import AudioClip


class StaticWordTimings:
    """A :class:`WordTimingProvider` over a fixed list of timings."""

    name: str = "static"

    def __init__(self, words: Sequence[WordTiming], *, label: str = "static") -> None:
        self._words = tuple(words)
        self.name = label

    def words_for(
        self, audio: AudioClip, *, transcript: str = ""
    ) -> Sequence[WordTiming]:
        return self._words


class WordTimingsLipSync:
    """:class:`LipSyncProvider` driven by a :class:`WordTimingProvider`.

    Skips transcription entirely. Use this when the caller already has
    authoritative word timings (e.g. from a separate lyric-alignment
    pipeline).

    Args:
        provider: any :class:`WordTimingProvider`.
        char_to_viseme: optional override of the character→viseme code
            mapping; defaults to the one shared with
            :class:`OfflineLipSync` / :class:`WhisperLipSync`.
        convention: declared viseme convention string for the produced
            track. Defaults to ``"rhubarb"`` for compatibility with the
            existing cutout adapter.
        rest_viseme: code emitted in silent gaps. Defaults to
            :data:`_REST_VISEME`.
        min_gap_for_rest: minimum inter-word silence (seconds) before
            we insert a rest keyframe. Defaults to ``0.20``.
    """

    convention: str = "rhubarb"

    def __init__(
        self,
        provider: WordTimingProvider,
        *,
        char_to_viseme: dict[str, str] | None = None,
        convention: str = "rhubarb",
        rest_viseme: str = _REST_VISEME,
        min_gap_for_rest: float = 0.20,
    ) -> None:
        self.provider = provider
        self.convention = convention
        self._mapping = (
            dict(char_to_viseme) if char_to_viseme else dict(_CHAR_TO_VISEME)
        )
        self._rest = rest_viseme
        self._min_gap = min_gap_for_rest

    @property
    def name(self) -> str:
        # Disambiguate cache keys per-provider so swapping providers
        # invalidates the viseme cache.
        return f"word-timings:{getattr(self.provider, 'name', 'unknown')}"

    #: Built from words, so the track carries them (an#96).
    emits_word_timings: bool = True

    def align(self, audio: AudioClip, transcript: str) -> VisemeTrack:
        words = list(self.provider.words_for(audio, transcript=transcript))
        return VisemeTrack(
            visemes=word_timings_to_visemes(
                words,
                total_duration=audio.duration,
                char_to_viseme=self._mapping,
                rest_viseme=self._rest,
                min_gap_for_rest=self._min_gap,
            ),
            convention=self.convention,
            duration=audio.duration,
            words=words,
        )
