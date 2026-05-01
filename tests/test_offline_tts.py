"""OfflineTTS produces a valid WAV of plausible duration."""

from __future__ import annotations

import io
import tempfile
import wave
from pathlib import Path

import pytest

from an.audio.offline_tts import OfflineTTS


def test_synthesize_returns_audio_clip_with_bytes():
    tts = OfflineTTS()
    clip = tts.synthesize("Hello world", voice_id="default")
    assert clip.bytes_ is not None
    assert len(clip.bytes_) > 44  # WAV header is 44 bytes
    assert clip.transcript == "Hello world"
    assert clip.voice_id == "default"


def test_wav_bytes_are_a_valid_wav_file():
    clip = OfflineTTS().synthesize("Hi")
    with wave.open(io.BytesIO(clip.bytes_), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 22050
        assert wf.getnframes() > 0


def test_duration_scales_with_text_length():
    short = OfflineTTS().synthesize("hi")
    long = OfflineTTS().synthesize("the quick brown fox jumps over the lazy dog")
    assert long.duration > short.duration


def test_min_duration_floor_for_empty_text():
    """Empty text shouldn't crash; produce at least the minimum duration."""
    clip = OfflineTTS().synthesize("")
    assert clip.duration >= 0.4


def test_path_argument_writes_wav_to_disk():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.wav"
        clip = OfflineTTS().synthesize("hi", path=out)
        assert out.exists()
        assert out.stat().st_size == len(clip.bytes_)


def test_list_voices_returns_at_least_default():
    voices = list(OfflineTTS().list_voices())
    assert any(v.voice_id == "default" for v in voices)


def test_silence_is_actually_silent():
    """All samples should be zero in the offline output."""
    clip = OfflineTTS().synthesize("test")
    with wave.open(io.BytesIO(clip.bytes_), "rb") as wf:
        n = wf.getnframes()
        frames = wf.readframes(n)
        # All bytes should be 0 (PCM zero-amplitude).
        assert frames == b"\x00" * len(frames)
