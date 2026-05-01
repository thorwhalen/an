"""ElevenLabsTTS — only runs when ELEVEN_API_KEY is set + elevenlabs installed."""

from __future__ import annotations

import importlib.util
import os

import pytest

from an.audio.elevenlabs_tts import ElevenLabsTTS


_HAS_PKG = importlib.util.find_spec("elevenlabs") is not None
_HAS_KEY = bool(os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY"))

pytestmark = pytest.mark.skipif(
    not (_HAS_PKG and _HAS_KEY),
    reason="needs elevenlabs package + ELEVEN_API_KEY",
)


def test_synthesize_short_text_returns_audio_bytes():
    tts = ElevenLabsTTS()
    clip = tts.synthesize("This is a test.")
    assert clip.bytes_ is not None
    assert len(clip.bytes_) > 0
    assert clip.transcript == "This is a test."
    # mp3 magic bytes start with ID3 or 0xFF 0xFB
    assert clip.bytes_[:3] == b"ID3" or clip.bytes_[:1] == b"\xff"


def test_no_api_key_raises_clear_error():
    tts = ElevenLabsTTS(api_key="")
    # explicitly empty key
    if os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY"):
        pytest.skip("env vars set; can't test missing-key path")
    with pytest.raises(RuntimeError, match="ELEVEN_API_KEY"):
        tts.synthesize("hi")
