"""Audio pipeline orchestration: produce_audio_for_dialogue + scene-walking."""

from __future__ import annotations

import tempfile

import pytest

from an import build_project_mall
from an.audio.pipeline import (
    default_lipsync,
    default_tts,
    produce_audio_for_dialogue,
    produce_audio_for_scene,
)
from an.ir.schema import Dialogue, Meta, SceneIR, Shot


def test_default_providers_are_offline():
    assert default_tts().name == "offline"
    assert default_lipsync().name == "offline"


def test_produce_audio_for_dialogue_returns_clip_and_track():
    line = Dialogue(speaker="charlie", text="Hello, world.")
    audio, track = produce_audio_for_dialogue(line)
    assert audio.duration > 0
    assert audio.bytes_ is not None
    assert len(track.visemes) >= 2


def test_produce_audio_for_scene_stamps_visemes():
    scene = SceneIR(
        meta=Meta(title="t", duration=5.0),
        timeline=[
            Shot(
                id="s1",
                renderer="cutout",
                duration=5.0,
                dialogue=[
                    Dialogue(speaker="charlie", text="Hi there."),
                    Dialogue(speaker="maya", text="Hello back."),
                ],
            )
        ],
    )
    produce_audio_for_scene(scene)
    line0, line1 = scene.timeline[0].dialogue
    assert line0.viseme_track is not None
    assert len(line0.viseme_track.keyframes) >= 2
    assert line0.duration is not None
    assert line0.start == pytest.approx(0.0)
    # Second line follows the first.
    assert line1.start == pytest.approx(line0.start + line0.duration)


def test_scene_walking_is_idempotent():
    """Re-running on an already-stamped scene shouldn't re-synthesize."""
    scene = SceneIR(
        timeline=[
            Shot(
                id="s1",
                renderer="cutout",
                duration=2.0,
                dialogue=[Dialogue(speaker="x", text="hi")],
            )
        ]
    )
    produce_audio_for_scene(scene)
    track1 = scene.timeline[0].dialogue[0].viseme_track
    duration1 = scene.timeline[0].dialogue[0].duration
    produce_audio_for_scene(scene)
    assert scene.timeline[0].dialogue[0].viseme_track is track1
    assert scene.timeline[0].dialogue[0].duration == duration1


def test_mall_caching_round_trip():
    """Produced audio should be persistable + reloadable from mall stores."""
    with tempfile.TemporaryDirectory() as d:
        mall = build_project_mall(d, ensure=True)
        line = Dialogue(speaker="x", text="cached test")
        audio1, track1 = produce_audio_for_dialogue(line, mall)
        # Audio store now has one entry.
        assert len(mall["audio"]) == 1
        assert len(mall["visemes"]) == 1
        # Second call hits cache, returns equivalent audio.
        audio2, track2 = produce_audio_for_dialogue(line, mall)
        assert audio1.bytes_ == audio2.bytes_
        assert audio1.duration == audio2.duration
        assert len(mall["audio"]) == 1  # no duplicate write
        # Viseme track equivalence
        assert [v.code for v in track1.visemes] == [v.code for v in track2.visemes]


def test_dialogue_with_explicit_start_is_respected():
    scene = SceneIR(
        timeline=[
            Shot(
                id="s1",
                renderer="cutout",
                duration=10.0,
                dialogue=[
                    Dialogue(speaker="x", text="late line", start=5.0),
                ],
            )
        ]
    )
    produce_audio_for_scene(scene)
    assert scene.timeline[0].dialogue[0].start == 5.0
