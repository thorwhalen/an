"""ElevenLabsTTS — real speech via the ElevenLabs API. Requires ELEVEN_API_KEY.

Lazily imports the elevenlabs SDK so the rest of `an` works without it.
If you want real speech, ``pip install elevenlabs`` and set
``ELEVEN_API_KEY`` (or ``ELEVENLABS_API_KEY``) in your environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from an.audio.tts import AudioClip, VoiceMeta


_DEFAULT_MODEL_ID: str = "eleven_turbo_v2_5"
_DEFAULT_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs's "Rachel"
_DEFAULT_OUTPUT_FORMAT: str = "mp3_44100_128"


class ElevenLabsTTS:
    """ElevenLabs-backed TTSProvider. Constructor takes an optional api_key
    (falls back to ``ELEVEN_API_KEY`` / ``ELEVENLABS_API_KEY``).

    Implements the ``TTSProvider`` protocol.
    """

    name: str = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str = _DEFAULT_MODEL_ID,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("ELEVEN_API_KEY")
            or os.environ.get("ELEVENLABS_API_KEY")
        )
        self.model_id = model_id
        self.output_format = output_format

    def _client(self):
        if not self.api_key:
            raise RuntimeError(
                "ElevenLabsTTS requires an API key. Set ELEVEN_API_KEY in your "
                "environment or pass api_key= to the constructor."
            )
        try:
            from elevenlabs.client import ElevenLabs  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "elevenlabs package not installed. Install with: pip install elevenlabs"
            ) from e
        return ElevenLabs(api_key=self.api_key)

    def synthesize(self, text: str, voice_id: str | None = None, **kw) -> AudioClip:
        client = self._client()
        # ElevenLabs has no voice named "default"; if the caller passes the
        # canonical "default" sentinel (or None), use this provider's pinned
        # default voice instead.
        effective_voice = (
            voice_id if voice_id and voice_id != "default" else _DEFAULT_VOICE_ID
        )
        # elevenlabs SDK 1.x exposes `text_to_speech.convert`.
        audio_iter = client.text_to_speech.convert(
            voice_id=effective_voice,
            text=text,
            model_id=self.model_id,
            output_format=self.output_format,
        )
        audio_bytes = b"".join(audio_iter)

        out_path = kw.get("path")
        if out_path is not None:
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(audio_bytes)

        # Estimate duration: mp3_44100_128 is 128kbps → 16 KB/s.
        bitrate_bytes_per_s = 128 * 1024 / 8 if "128" in self.output_format else 16_000
        duration = len(audio_bytes) / bitrate_bytes_per_s if audio_bytes else 0.0

        return AudioClip(
            path=out_path,
            bytes_=audio_bytes,
            duration=duration,
            sample_rate=44100,
            channels=1,
            voice_id=effective_voice,
            transcript=text,
        )

    def list_voices(self) -> Iterable[VoiceMeta]:
        try:
            client = self._client()
        except RuntimeError:
            return []
        try:
            voices = client.voices.get_all().voices
        except Exception:
            return []
        out: list[VoiceMeta] = []
        for v in voices:
            out.append(
                VoiceMeta(
                    voice_id=getattr(v, "voice_id", ""),
                    name=getattr(v, "name", ""),
                    provider=self.name,
                    language="en",
                )
            )
        return out
