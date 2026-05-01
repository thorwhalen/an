"""compile_shot emits viseme channels for dialogue lines."""

from __future__ import annotations

import pytest

from an.adapters.cutout.compile import compile_shot
from an.ir.schema import (
    AssetRef,
    Dialogue,
    Shot,
    VisemeKeyframe,
    VisemeTrack as IRVisemeTrack,
)


def _entity(speaker: str = "charlie") -> AssetRef:
    return AssetRef(
        kind="character", id=speaker, store="characters", ref=f"{speaker}-v1"
    )


def _line_with_visemes(speaker="charlie") -> Dialogue:
    return Dialogue(
        speaker=speaker,
        text="hi",
        start=0.0,
        duration=1.0,
        viseme_track=IRVisemeTrack(
            keyframes=[
                VisemeKeyframe(time=0.0, viseme="X"),
                VisemeKeyframe(time=0.3, viseme="A"),
                VisemeKeyframe(time=0.7, viseme="C"),
            ]
        ),
    )


def test_viseme_track_produces_channel_on_mouth():
    shot = Shot(
        id="s1",
        style="cutout",
        duration=2.0,
        entities=[_entity()],
        dialogue=[_line_with_visemes()],
    )
    j = compile_shot(shot)
    # There should be at least one animation whose channel targets head/mouth.
    mouth_anims = [
        a for a in j.animations.values()
        if any(c.target.endswith("head/mouth") for c in a.channels)
    ]
    assert len(mouth_anims) == 1
    anim = mouth_anims[0]
    ch = anim.channels[0]
    assert ch.property == "viseme"
    assert all(kf.easing == "step" for kf in ch.keyframes)
    # Includes the explicit keyframes plus a closing rest at duration.
    assert ch.keyframes[0].value == "X"
    assert ch.keyframes[-1].value == "X"


def test_viseme_track_added_to_speaker_track():
    shot = Shot(
        id="s1",
        style="cutout",
        duration=2.0,
        entities=[_entity("maya")],
        dialogue=[_line_with_visemes("maya")],
    )
    j = compile_shot(shot)
    maya_tracks = [t for t in j.timeline.tracks if t.target_root == "maya"]
    assert len(maya_tracks) == 1
    # The placed viseme clip starts at line.start (0.0) and lasts duration (1.0).
    placed = maya_tracks[0].clips[0]
    assert placed.start_time == pytest.approx(0.0)
    assert placed.duration == pytest.approx(1.0)


def test_dialogue_without_viseme_track_skipped():
    """No visemes → no mouth channel emitted."""
    shot = Shot(
        id="s1",
        style="cutout",
        duration=2.0,
        entities=[_entity()],
        dialogue=[Dialogue(speaker="charlie", text="silent")],
    )
    j = compile_shot(shot)
    assert not any(
        any(c.property == "viseme" for c in a.channels)
        for a in j.animations.values()
    )


def test_head_node_has_mouth_child():
    """The placeholder character should now expose a mouth child node."""
    shot = Shot(
        id="s1",
        style="cutout",
        duration=1.0,
        entities=[_entity()],
    )
    j = compile_shot(shot)
    char_node = j.scene.children[0]
    head = next(c for c in char_node.children if c.name == "head")
    mouth = next((c for c in head.children if c.name == "mouth"), None)
    assert mouth is not None
    assert mouth.visual is not None
    assert mouth.visual.kind == "rect"
