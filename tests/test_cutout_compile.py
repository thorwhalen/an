"""compile_shot: end-to-end Shot -> CutoutSceneJSON."""

from __future__ import annotations

import pytest

from an.adapters.cutout.compile import compile_shot
from an.adapters.cutout.serialize import from_dict, to_dict
from an.ir.compose import sequence, tween, set_, delay
from an.ir.schema import AssetRef, Shot


def test_empty_shot_compiles_to_minimal_scene():
    shot = Shot(id="s1", renderer="cutout", duration=3.0)
    j = compile_shot(shot)
    assert j.timeline.duration == 3.0
    assert j.scene.name == "root"
    assert j.scene.children == []
    assert j.timeline.tracks == []
    assert j.animations == {}


def test_non_cutout_style_rejected():
    shot = Shot(id="s1", renderer="manim", duration=1.0)
    with pytest.raises(ValueError, match="cutout"):
        compile_shot(shot)


def test_character_entity_creates_subtree():
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=2.0,
        entities=[
            AssetRef(
                kind="character", id="charlie", store="characters", ref="charlie-v1"
            )
        ],
    )
    j = compile_shot(shot, mall={"characters": {}})
    # One character node under root, with placeholder parts.
    assert len(j.scene.children) == 1
    char = j.scene.children[0]
    assert char.name == "charlie"
    part_names = [c.name for c in char.children]
    assert "head" in part_names
    assert "torso" in part_names
    # Head has a mouth CHILD NODE for lip-sync — the node path
    # charlie/head/mouth is what viseme channels target. (This used to also
    # assert the vestigial NodeJSON.slots entry; that field was serialized
    # dead weight the runtime never read, deleted in an#86.)
    head = next(c for c in char.children if c.name == "head")
    mouth = next((c for c in head.children if c.name == "mouth"), None)
    assert mouth is not None
    assert mouth.visual is not None and mouth.visual.kind == "mouth"


def test_character_uses_store_provided_parts_when_present():
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        entities=[
            AssetRef(kind="character", id="maya", store="characters", ref="maya-v1")
        ],
    )
    mall = {"characters": {"maya-v1": {"name": "Maya", "parts": ["head", "body"]}}}
    j = compile_shot(shot, mall=mall)
    char = j.scene.children[0]
    assert [c.name for c in char.children] == ["head", "body"]


def test_tween_compiles_to_animation_plus_placed_clip():
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=2.0,
        actions=[tween("charlie/torso", "rotation", to=1.5, duration=1.0)],
    )
    j = compile_shot(shot)
    # One animation registered, one placed clip on one track.
    assert len(j.animations) == 1
    anim_id, anim = next(iter(j.animations.items()))
    assert anim.duration == 1.0
    assert len(anim.channels) == 1
    ch = anim.channels[0]
    assert ch.target == "charlie/torso"
    assert ch.property == "rotation"
    assert len(ch.keyframes) == 2
    assert ch.keyframes[0].value == 0.0
    assert ch.keyframes[1].value == 1.5
    assert len(j.timeline.tracks) == 1
    track = j.timeline.tracks[0]
    assert track.target_root == "charlie"
    assert len(track.clips) == 1
    assert track.clips[0].animation_id == anim_id
    assert track.clips[0].start_time == 0.0


def test_sequence_flattens_to_correct_start_times():
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=3.0,
        actions=[
            sequence(
                tween("a/x", "rotation", to=1.0, duration=1.0),
                delay(0.5),
                tween("a/x", "rotation", to=0.0, duration=1.0),
            )
        ],
    )
    j = compile_shot(shot)
    assert len(j.timeline.tracks) == 1
    placed_clips = j.timeline.tracks[0].clips
    starts = sorted(p.start_time for p in placed_clips)
    assert starts == pytest.approx([0.0, 1.5])


def test_set_action_compiles_to_step_keyframe():
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=1.0,
        actions=[set_("a", "x", 5.0, at=0.5)],
    )
    j = compile_shot(shot)
    assert len(j.animations) == 1
    anim = next(iter(j.animations.values()))
    assert anim.channels[0].keyframes[0].value == 5.0
    assert anim.channels[0].keyframes[0].easing == "step"
    placed = j.timeline.tracks[0].clips[0]
    assert placed.start_time == 0.5


def test_compile_output_round_trips_through_serialize():
    shot = Shot(
        id="s1",
        renderer="cutout",
        duration=2.0,
        entities=[
            AssetRef(kind="character", id="c", store="characters", ref="c-v1")
        ],
        actions=[tween("c/torso", "rotation", to=1.0, duration=1.0)],
    )
    j = compile_shot(shot)
    j2 = from_dict(to_dict(j))
    assert j == j2


def test_compiler_refuses_bool_and_none_keyframe_values():
    """The two value classes the spec and the runtime evaluate differently.

    Python would lerp a bool (``bool ⊂ int``) where JS's ``typeof`` snaps it;
    the Python spec carries ``None`` into the pose where the runtime drops
    null values. The compiler refuses both at emission (an#86).
    """
    import pytest as _pytest

    from an.adapters.cutout.compile import CutoutCompileError
    from an.ir.schema import SetAction, TweenAction

    def _shot(action):
        return Shot(id="s", renderer="cutout", duration=1.0, actions=[action])

    for bad in (True, False, None):
        with _pytest.raises(CutoutCompileError, match="bool and None"):
            compile_shot(_shot(SetAction(target="a", property="x", value=bad)))
    with _pytest.raises(CutoutCompileError, match="bool and None"):
        compile_shot(
            _shot(
                TweenAction(
                    target="a", property="x", from_value=0.0, to_value=True
                )
            )
        )
    # from_value is a SEPARATE guarded call site — deleting just that check
    # left the suite green until this case existed (an#86 adversarial review).
    # None never reaches it (a None from_value means "start from rest"), so
    # bool is the live branch there.
    with _pytest.raises(CutoutCompileError, match="bool and None"):
        compile_shot(
            _shot(
                TweenAction(
                    target="a", property="x", from_value=True, to_value=1.0
                )
            )
        )
