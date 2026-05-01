"""RhubarbLipSync — only runs when the rhubarb binary is on PATH."""

from __future__ import annotations

import shutil

import pytest

from an.audio.offline_tts import OfflineTTS
from an.audio.rhubarb_lipsync import RhubarbLipSync


pytestmark = pytest.mark.skipif(
    shutil.which("rhubarb") is None,
    reason="rhubarb binary not installed",
)


def test_align_real_audio_via_rhubarb():
    audio = OfflineTTS().synthesize("Hello there friend.")
    track = RhubarbLipSync().align(audio, "Hello there friend.")
    assert track.convention == "rhubarb"
    # Even silent audio yields at least a few cues.
    assert len(track.visemes) >= 1


def test_missing_binary_raises_clear_error():
    """Construct with explicit None binary to force the error path."""
    rls = RhubarbLipSync(binary_path=None)
    rls.binary_path = None  # belt and suspenders
    audio = OfflineTTS().synthesize("hi")
    with pytest.raises(RuntimeError, match="rhubarb"):
        rls.align(audio, "hi")
