"""OfflineLipSync produces deterministic viseme tracks."""

from __future__ import annotations

import pytest

from an.audio.lipsync import VisemeTrack
from an.audio.offline_lipsync import OfflineLipSync
from an.audio.tts import AudioClip


def _clip(duration: float = 1.0) -> AudioClip:
    return AudioClip(duration=duration)


def test_align_returns_viseme_track():
    track = OfflineLipSync().align(_clip(), "hello")
    assert isinstance(track, VisemeTrack)
    assert track.convention == "rhubarb"


def test_track_starts_and_ends_with_rest():
    track = OfflineLipSync().align(_clip(2.0), "abc")
    assert track.visemes[0].code == "X"
    assert track.visemes[-1].code == "X"
    assert track.visemes[-1].time == pytest.approx(2.0)


def test_empty_transcript_yields_just_rest():
    track = OfflineLipSync().align(_clip(1.0), "")
    assert all(v.code == "X" for v in track.visemes)
    assert len(track.visemes) >= 2


def test_repeated_chars_collapse_to_one_keyframe():
    """'aaaa' should not produce four 'D' keyframes."""
    track = OfflineLipSync().align(_clip(1.0), "aaaa")
    non_rest = [v for v in track.visemes if v.code != "X"]
    assert len(non_rest) == 1
    assert non_rest[0].code == "D"  # 'a' maps to D in our table


def test_viseme_codes_are_in_rhubarb_alphabet():
    track = OfflineLipSync().align(_clip(2.0), "the quick brown fox")
    for v in track.visemes:
        assert v.code in {"A", "B", "C", "D", "E", "F", "G", "H", "X"}


def test_keyframes_are_within_duration():
    duration = 3.0
    track = OfflineLipSync().align(_clip(duration), "speak some text")
    for v in track.visemes:
        assert 0.0 <= v.time <= duration


def test_keyframes_are_monotonic_in_time():
    track = OfflineLipSync().align(_clip(2.0), "monotonic times")
    times = [v.time for v in track.visemes]
    assert times == sorted(times)


def test_known_consonants_map_predictably():
    track = OfflineLipSync().align(_clip(1.0), "f")
    codes = [v.code for v in track.visemes if v.code != "X"]
    assert codes == ["G"]  # 'f' is teeth-on-lip
    track2 = OfflineLipSync().align(_clip(1.0), "p")
    codes2 = [v.code for v in track2.visemes if v.code != "X"]
    assert codes2 == ["A"]  # 'p' is lips-closed
