"""Lip-sync provider protocol + viseme dataclasses.

Viseme conventions are renderer-agnostic in the IR; concrete providers in
later phases (Rhubarb, Azure, MFA) emit their own letter/number encodings,
which the cutout adapter then maps to mouth-slot attachments.

Two protocols live here:

- :class:`LipSyncProvider` — the head-to-toe pipeline ("audio + transcript
  → visemes"). Owns its own transcription if it needs one. Most cutout
  callers go through this.
- :class:`WordTimingProvider` — the narrower "audio → word timings"
  contract. Lets external callers (e.g. ``muvid``, with its own lyric
  alignment store) skip a redundant transcription pass and feed timings
  directly into :class:`WordTimingsLipSync`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence, runtime_checkable

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


#: One word's slice in time. Tuple form keeps providers cheap.
WordTiming = tuple[str, float, float]  # (text, start_s, end_s)


@runtime_checkable
class LipSyncProvider(Protocol):
    """Audio + transcript → aligned viseme track."""

    name: str
    convention: str  # which viseme convention this provider emits

    def align(self, audio: AudioClip, transcript: str) -> VisemeTrack:
        """Produce a viseme track for ``audio`` given its ``transcript``."""


@runtime_checkable
class WordTimingProvider(Protocol):
    """Audio → ``[(word, start_s, end_s), ...]``.

    Implementations may run a transcriber (Whisper / Scribe) or look up
    pre-computed timings (``muvid``'s lacing store). Returned tuples are
    expected to be in ascending start-time order; gaps between words are
    fine and represent silence the lipsync provider should rest through.
    """

    name: str

    def words_for(
        self, audio: AudioClip, *, transcript: str = ""
    ) -> Sequence[WordTiming]:
        """Return the word timings for ``audio``."""


# --- shared word-timings → visemes conversion ----------------------------

_DEFAULT_REST_VISEME = "X"
_DEFAULT_MIN_WORD_GAP_FOR_REST = 0.20  # seconds


def word_timings_to_visemes(
    words: Iterable[WordTiming],
    *,
    total_duration: float,
    char_to_viseme: dict[str, str],
    rest_viseme: str = _DEFAULT_REST_VISEME,
    min_gap_for_rest: float = _DEFAULT_MIN_WORD_GAP_FOR_REST,
) -> list[Viseme]:
    """Distribute viseme keyframes across word boundaries.

    Used by :class:`WhisperLipSync` and :class:`WordTimingsLipSync`. The
    algorithm: per word, walk its characters in order, look up each
    character's viseme code, dedupe consecutive identical codes, and
    space the resulting keyframes evenly across the word's
    ``[start, end]`` interval. In gaps wider than ``min_gap_for_rest``
    insert a single rest keyframe just after the previous word ended.

    Times are clamped to ``[0, total_duration]`` since some
    transcribers occasionally round the last word's end past the
    audio's actual length.
    """
    max_t = max(0.0, total_duration if total_duration > 0 else 0.0)

    def clamp(t: float) -> float:
        return max(0.0, min(t, max_t))

    out: list[Viseme] = [Viseme(time=0.0, code=rest_viseme)]
    prev_end = 0.0
    for raw_word, w_start, w_end in words:
        w_start = clamp(w_start)
        w_end = clamp(w_end)
        if w_start - prev_end > min_gap_for_rest:
            out.append(Viseme(time=clamp(prev_end + 0.05), code=rest_viseme))

        codes: list[str] = []
        previous: str | None = None
        for ch in raw_word.lower().strip():
            if not ch.isalpha():
                continue
            code = char_to_viseme.get(ch, "C")
            if code == previous:
                continue
            previous = code
            codes.append(code)

        if not codes:
            prev_end = w_end
            continue
        n = len(codes)
        duration = max(0.05, w_end - w_start)
        step = duration / n
        for i, code in enumerate(codes):
            out.append(Viseme(time=clamp(w_start + i * step), code=code))
        prev_end = w_end

    out.append(Viseme(time=max_t, code=rest_viseme))
    return out
