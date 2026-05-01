"""OfflineTTS — produces silent audio of plausible duration. No network, no keys.

This is the **default** TTS provider for `an`. It's honest about being silent
(it doesn't pretend to speak), but it produces a real WAV file with the right
duration so the rest of the pipeline (lip-sync, rendering, mp4 muxing) runs
end-to-end without any API setup.

Use ``ElevenLabsTTS`` for real speech once you've set ``ELEVEN_API_KEY``.

>>> tts = OfflineTTS()
>>> clip = tts.synthesize("Hello, world!", voice_id="default")
>>> clip.duration > 0.0
True
>>> clip.transcript
'Hello, world!'
"""

from __future__ import annotations

import struct
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from an.audio.tts import AudioClip, VoiceMeta


# Tunables (no magic numbers below).
_DEFAULT_SAMPLE_RATE: int = 22050
_DEFAULT_CHANNELS: int = 1
_BYTES_PER_SAMPLE: int = 2  # 16-bit PCM
_MIN_DURATION_S: float = 0.4
_MAX_DURATION_S: float = 60.0
_SECONDS_PER_CHAR: float = 0.06  # ~10 cps speech rate proxy
_LEADING_SILENCE_S: float = 0.05  # tiny pad for natural feel


class OfflineTTS:
    """Default TTS provider: silent WAV of length proportional to text.

    Implements the ``TTSProvider`` protocol.
    """

    name: str = "offline"

    def __init__(
        self,
        *,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        channels: int = _DEFAULT_CHANNELS,
        seconds_per_char: float = _SECONDS_PER_CHAR,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.seconds_per_char = seconds_per_char

    # -- TTSProvider interface -----------------------------------------------

    def synthesize(self, text: str, voice_id: str = "default", **kw) -> AudioClip:
        target_duration = self._estimate_duration(text)
        n_frames = int(round(target_duration * self.sample_rate))
        # Use the post-rounding duration so cache reload (which derives from
        # frames/rate) matches the in-memory clip.
        duration = n_frames / self.sample_rate if self.sample_rate else 0.0
        wav_bytes = self._silent_wav_bytes_for_frames(n_frames)
        out_path = kw.get("path")
        if out_path is not None:
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(wav_bytes)
        return AudioClip(
            path=Path(out_path) if out_path else None,
            bytes_=wav_bytes,
            duration=duration,
            sample_rate=self.sample_rate,
            channels=self.channels,
            voice_id=voice_id,
            transcript=text,
        )

    def list_voices(self) -> Iterable[VoiceMeta]:
        return [
            VoiceMeta(
                voice_id="default",
                name="Offline (silent placeholder)",
                provider=self.name,
                language="en",
                extra={"note": "produces silent WAV of plausible duration"},
            )
        ]

    # -- internals -----------------------------------------------------------

    def _estimate_duration(self, text: str) -> float:
        """Approximate duration: leading pad + per-char accumulation, clamped."""
        meaningful = sum(1 for ch in text if not ch.isspace())
        raw = _LEADING_SILENCE_S + meaningful * self.seconds_per_char
        return max(_MIN_DURATION_S, min(_MAX_DURATION_S, raw))

    def _silent_wav_bytes_for_frames(self, n_frames: int) -> bytes:
        """Build a valid WAV file (in-memory) of pure silence with ``n_frames`` samples."""
        import io

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(_BYTES_PER_SAMPLE)
            wf.setframerate(self.sample_rate)
            silent_frame = struct.pack("<h", 0) * self.channels
            wf.writeframes(silent_frame * n_frames)
        return buf.getvalue()
