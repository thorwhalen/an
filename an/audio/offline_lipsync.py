"""OfflineLipSync — deterministic transcript → viseme track. No network, no binary.

The default lip-sync provider for `an`. Maps each meaningful character of the
transcript to a Rhubarb-convention viseme letter (A–H + X), then distributes
keyframes evenly across the audio's duration. Repeated visemes get collapsed
into a single keyframe so the mouth doesn't "stutter" on long vowel runs.

Crude but visible. Use ``RhubarbLipSync`` for real phoneme alignment once
you've installed the Rhubarb binary.

>>> from an.audio.tts import AudioClip
>>> ls = OfflineLipSync()
>>> track = ls.align(AudioClip(duration=1.0, transcript="hello"), "hello")
>>> track.convention
'rhubarb'
>>> track.duration
1.0
>>> len(track.visemes) >= 2
True
"""

from __future__ import annotations

from typing import Iterable

from an.audio.lipsync import Viseme, VisemeTrack
from an.audio.tts import AudioClip


# Map a single ASCII character (lowercased) to a Rhubarb viseme letter.
# A=closed (P/B/M, rest), B=tight open (D/S/T/Z/N/L), C=eh, D=ah/wide,
# E=ohh, F=ooh, G=teeth-on-lip (F/V), H=th, X=idle.
_CHAR_TO_VISEME: dict[str, str] = {
    # closed lips
    "p": "A",
    "b": "A",
    "m": "A",
    # teeth-on-lip
    "f": "G",
    "v": "G",
    # th-ish
    "h": "C",
    # vowel families
    "a": "D",
    "e": "C",
    "i": "B",
    "o": "E",
    "u": "F",
    "y": "B",
    # alveolar consonants
    "d": "B",
    "t": "B",
    "s": "B",
    "z": "B",
    "n": "B",
    "l": "B",
    "r": "B",
    # remaining consonants — neutral semi-open
    "c": "C",
    "g": "C",
    "j": "C",
    "k": "C",
    "q": "C",
    "w": "F",
    "x": "C",
}
_REST_VISEME: str = "X"


class OfflineLipSync:
    """Default lip-sync provider: deterministic char-to-viseme mapping.

    Implements the ``LipSyncProvider`` protocol.
    """

    name: str = "offline"
    convention: str = "rhubarb"

    def __init__(self, *, char_to_viseme: dict[str, str] | None = None) -> None:
        self._mapping = (
            dict(char_to_viseme) if char_to_viseme else dict(_CHAR_TO_VISEME)
        )

    def align(self, audio: AudioClip, transcript: str) -> VisemeTrack:
        codes = list(self._codes_for(transcript))
        if not codes:
            # Empty transcript → just rest at start, rest at end.
            return VisemeTrack(
                visemes=[
                    Viseme(time=0.0, code=_REST_VISEME),
                    Viseme(time=max(0.0, audio.duration), code=_REST_VISEME),
                ],
                convention=self.convention,
                duration=audio.duration,
            )
        # Distribute one keyframe per code over [0, duration].
        n = len(codes)
        duration = max(audio.duration, 1e-3)
        visemes: list[Viseme] = []
        # Always lead with rest at t=0
        visemes.append(Viseme(time=0.0, code=_REST_VISEME))
        for i, code in enumerate(codes):
            t = (i + 1) / (n + 1) * duration
            visemes.append(Viseme(time=t, code=code))
        # Trailing rest
        visemes.append(Viseme(time=duration, code=_REST_VISEME))
        return VisemeTrack(
            visemes=visemes, convention=self.convention, duration=audio.duration
        )

    def _codes_for(self, transcript: str) -> Iterable[str]:
        """Per-character viseme codes; collapses adjacent duplicates."""
        previous: str | None = None
        for raw in transcript:
            ch = raw.lower()
            if not ch.isalpha():
                # Spaces, punctuation → mouth rest. Insert if not redundant.
                if previous != _REST_VISEME:
                    previous = _REST_VISEME
                    yield _REST_VISEME
                continue
            code = self._mapping.get(ch, "C")
            if code == previous:
                continue
            previous = code
            yield code
