"""``WordTimingsLipSync`` consumes pre-computed timings instead of re-transcribing."""

from __future__ import annotations

import pytest

from an.audio.injectable_lipsync import StaticWordTimings, WordTimingsLipSync
from an.audio.lipsync import (
    LipSyncProvider,
    Viseme,
    VisemeTrack,
    WordTimingProvider,
    word_timings_to_visemes,
)
from an.audio.offline_lipsync import _CHAR_TO_VISEME
from an.audio.tts import AudioClip


def test_word_timings_to_visemes_emits_rest_at_boundaries():
    """Visemes start with a rest at t=0 and end with a rest at total_duration."""
    visemes = word_timings_to_visemes(
        [("hello", 0.5, 1.0)],
        total_duration=2.0,
        char_to_viseme=_CHAR_TO_VISEME,
    )
    assert visemes[0].time == 0.0
    assert visemes[0].code == "X"
    assert visemes[-1].time == pytest.approx(2.0)
    assert visemes[-1].code == "X"


def test_word_timings_to_visemes_clamps_past_total_duration():
    """Word ends past total_duration are clamped, not dropped."""
    visemes = word_timings_to_visemes(
        [("hi", 0.0, 5.0)],
        total_duration=2.0,
        char_to_viseme=_CHAR_TO_VISEME,
    )
    assert all(v.time <= 2.0 for v in visemes)


def test_word_timings_to_visemes_inserts_rest_in_long_gaps():
    """A wide silence between words gets an extra rest viseme."""
    visemes = word_timings_to_visemes(
        [("a", 0.0, 0.5), ("b", 5.0, 5.5)],
        total_duration=6.0,
        char_to_viseme=_CHAR_TO_VISEME,
        min_gap_for_rest=0.20,
    )
    # 1 leading rest + visemes for "a" + gap rest + visemes for "b" + final rest.
    rest_codes = [v for v in visemes if v.code == "X"]
    assert len(rest_codes) >= 3


def test_static_word_timings_implements_protocol():
    timings = [("hi", 0.0, 0.3)]
    provider = StaticWordTimings(timings)
    assert isinstance(provider, WordTimingProvider)
    out = provider.words_for(AudioClip(duration=1.0))
    assert list(out) == timings


def test_word_timings_lipsync_implements_protocol():
    provider = StaticWordTimings([("hi", 0.0, 0.3)])
    lipsync = WordTimingsLipSync(provider)
    assert isinstance(lipsync, LipSyncProvider)


def test_word_timings_lipsync_uses_injected_timings_not_transcription():
    """The provider's words drive the visemes — no whisper, no audio decoding."""
    audio = AudioClip(duration=2.0, transcript="ignored")
    provider = StaticWordTimings([("ma", 0.5, 0.8), ("mp", 1.0, 1.5)])
    lipsync = WordTimingsLipSync(provider)
    track = lipsync.align(audio, transcript="ignored too")
    assert isinstance(track, VisemeTrack)
    assert track.duration == 2.0
    # Both 'ma' and 'mp' contribute "A" visemes (m, p map to A).
    a_visemes = [v for v in track.visemes if v.code == "A"]
    assert a_visemes, "expected A visemes from m/p chars"


def test_word_timings_lipsync_name_disambiguates_provider():
    """The lipsync .name embeds the provider name so cache keys diverge."""
    a = WordTimingsLipSync(StaticWordTimings([], label="lacing"))
    b = WordTimingsLipSync(StaticWordTimings([], label="whisperx"))
    assert a.name != b.name
    assert "lacing" in a.name
    assert "whisperx" in b.name


def test_word_timings_lipsync_emits_no_visemes_for_empty_timings():
    """No words → just the boundary rests."""
    audio = AudioClip(duration=1.0, transcript="")
    lipsync = WordTimingsLipSync(StaticWordTimings([]))
    track = lipsync.align(audio, transcript="")
    assert all(v.code == "X" for v in track.visemes)


def test_custom_char_to_viseme_overrides_mapping():
    custom = {"a": "Z"}  # synthetic code
    audio = AudioClip(duration=1.0, transcript="aa")
    lipsync = WordTimingsLipSync(
        StaticWordTimings([("a", 0.0, 0.5)]),
        char_to_viseme=custom,
    )
    track = lipsync.align(audio, transcript="a")
    assert any(v.code == "Z" for v in track.visemes)
