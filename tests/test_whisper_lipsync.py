"""WhisperLipSync — skip-test if faster-whisper not installed."""

from __future__ import annotations

import importlib.util

import pytest

from an.audio.tts import AudioClip
from an.audio.offline_tts import OfflineTTS
from an.audio.whisper_lipsync import WhisperLipSync


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None,
    reason="faster-whisper not installed",
)


def test_align_silent_audio_returns_track():
    """Silent audio should still produce a viseme track (rest only)."""
    audio = OfflineTTS().synthesize("hello world")  # silent WAV
    track = WhisperLipSync().align(audio, "hello world")
    assert track.convention == "rhubarb"
    # All visemes should be rest (X) since the audio is silent and no words detected.
    codes = {v.code for v in track.visemes}
    assert "X" in codes


def test_align_visemes_within_audio_duration():
    audio = OfflineTTS().synthesize("a quick test of the system")
    track = WhisperLipSync().align(audio, "a quick test of the system")
    for v in track.visemes:
        assert 0.0 <= v.time <= audio.duration + 1e-3


def test_align_uses_known_viseme_alphabet():
    audio = OfflineTTS().synthesize("speak now please")
    track = WhisperLipSync().align(audio, "speak now please")
    for v in track.visemes:
        assert v.code in {"A", "B", "C", "D", "E", "F", "G", "H", "X"}


def test_align_with_real_speech_via_elevenlabs():
    """If ElevenLabs is configured, run an end-to-end real-speech test."""
    import os
    if not (
        os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    ):
        pytest.skip("no ElevenLabs API key — skipping real-speech path")
    if importlib.util.find_spec("elevenlabs") is None:
        pytest.skip("elevenlabs SDK not installed")

    from an.audio.elevenlabs_tts import ElevenLabsTTS

    audio = ElevenLabsTTS().synthesize("Hello there friend, how are you today?")
    track = WhisperLipSync().align(audio, "Hello there friend, how are you today?")
    # Real speech → multiple words → multiple non-rest visemes.
    non_rest = [v for v in track.visemes if v.code != "X"]
    assert len(non_rest) >= 3
