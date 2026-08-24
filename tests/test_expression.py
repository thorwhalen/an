"""The `an.expression` package: axes, presets, binding, provider, blendshapes (an#98).

Renderer-free. The compile-level behaviour is `tests/test_expression_compose.py`.
"""

from __future__ import annotations

import warnings

import pytest

from an.characters.schema import CharacterDescriptor
from an.expression import (
    AXES,
    BLENDSHAPE_V2_NAMES,
    PRESETS,
    ChannelBinding,
    DefaultExpressionProvider,
    ExpressionResolutionError,
    SetBinding,
    binding_for,
    clamp_axes,
    default_binding,
    expression_problems,
    expression_spans,
    from_blendshapes,
    known_presets,
    lid_key,
    preset_axes,
    resolve_mouth_set,
)
from an.expression.axes import LID_CLOSED_BELOW, LID_HALF_BELOW, LID_WIDE_ABOVE
from an.expression.provider import DIALOGUE_EMOTION_BLEND_S
from an.ir.compose import expression, sequence, delay
from an.ir.schema import AssetRef, Dialogue, Shot

# ---------------------------------------------------------------- axes


def test_every_axis_clamps_to_its_declared_range():
    for name, axis in AXES.items():
        assert axis.clamp(axis.hi + 5) == axis.hi
        assert axis.clamp(axis.lo - 5) == axis.lo
        assert axis.rest == 0.0, name


def test_lid_ladder_degrades_to_the_art_the_rig_has():
    full = {"WIDE", "OPEN", "HALF", "CLOSED"}
    assert lid_key(LID_WIDE_ABOVE + 0.01, available=full) == "WIDE"
    assert lid_key(LID_HALF_BELOW - 0.01, available=full) == "HALF"
    assert lid_key(LID_CLOSED_BELOW - 0.01, available=full) == "CLOSED"
    assert lid_key(0.0, available=full) == "OPEN"
    basic = {"OPEN", "CLOSED"}
    assert lid_key(LID_WIDE_ABOVE + 0.01, available=basic) == "OPEN"
    assert lid_key(LID_HALF_BELOW - 0.01, available=basic) == "OPEN"
    assert lid_key(LID_CLOSED_BELOW - 0.01, available=basic) == "CLOSED"
    assert lid_key(-1.0, available={"open", "closed"}) == "CLOSED", "keys are case-insensitive"


def test_an_unknown_axis_is_refused_by_name():
    with pytest.raises(ValueError, match="eyebrow"):
        clamp_axes({"eyebrow": 1.0})


# ---------------------------------------------------------------- presets


def test_every_name_the_old_brow_table_accepted_is_a_preset():
    """`_EMOTION_BROWS` is retired; its eight names (live content authors
    `amused`) must still resolve, or scenes change meaning silently."""
    old = {"neutral", "happy", "sad", "angry", "surprised", "skeptical", "amused", "thinking"}
    assert old <= set(known_presets())


def test_presets_carry_no_gaze():
    """Gaze is a separate source: 'thinking looks away' is a gaze action."""
    for p in PRESETS.values():
        assert not {"gaze_x", "gaze_y"} & set(p.axes), p.name


def test_preset_axes_scale_override_and_clamp():
    assert preset_axes("surprised")["brow_height_l"] == 1.0
    assert preset_axes("surprised", intensity=0.5)["brow_height_l"] == 0.5
    assert preset_axes("surprised", axes={"brow_height_l": 3.0})["brow_height_l"] == 1.0
    assert preset_axes("neutral") == {}
    with pytest.raises(ValueError, match="joyful"):
        preset_axes("joyful")


def test_amused_is_a_lighter_happy():
    happy, amused = preset_axes("happy"), preset_axes("amused")
    for axis, value in amused.items():
        assert abs(value) < abs(happy[axis]) and (value > 0) == (happy[axis] > 0), axis
    assert PRESETS["amused"].mouth_form == "happy"


# ---------------------------------------------------------------- binding


def test_default_binding_follows_the_slots_the_rig_has():
    desc = CharacterDescriptor(name="m")
    bound = default_binding(desc)
    props = {(b.axis, b.slot, b.property) for b in bound if isinstance(b, ChannelBinding)}
    assert ("brow_height_l", "left_brow", "y") in props
    assert ("brow_angle_r", "right_brow", "rotation") in props
    lids = {(b.axis, b.slot) for b in bound if isinstance(b, SetBinding)}
    assert lids == {("lid_open_l", "left_eye"), ("lid_open_r", "right_eye")}
    assert not [b for b in bound if b.axis.startswith("gaze")], "no pupil slot, no gaze binding"


def test_brow_angle_gains_carry_opposite_screen_signs():
    """+ is 'inner end up' on both sides; the screen rotations differ in sign."""
    gains = {b.slot: b.gain for b in default_binding(CharacterDescriptor(name="m")) if b.axis.startswith("brow_angle")}
    assert gains["left_brow"] == -gains["right_brow"] and gains["left_brow"] < 0


def test_a_rig_without_brows_binds_no_brow_axis():
    desc = CharacterDescriptor(name="m")
    desc.slots = [s for s in desc.slots if "brow" not in s.name]
    assert not [b for b in default_binding(desc) if b.axis.startswith("brow")]


def test_a_declared_binding_replaces_the_default_and_refuses_unknown_axes():
    desc = CharacterDescriptor(name="m")
    desc.expression_binding = [{"axis": "brow_height_l", "slot": "left_brow", "property": "y", "gain": -3.0, "rig_scaled": True}]
    (b,) = binding_for(desc)
    assert b == ChannelBinding("brow_height_l", "left_brow", "y", -3.0, rig_scaled=True)
    desc.expression_binding = [{"axis": "nose", "slot": "head", "property": "y", "gain": 1.0}]
    with pytest.raises(ExpressionResolutionError, match="nose"):
        binding_for(desc)


def _with_variant(desc: CharacterDescriptor, form: str, keys=None) -> CharacterDescriptor:
    neutral = desc.asset_sets["viseme"]
    desc.asset_sets[f"viseme@{form}"] = {k: f"{v}_{form}" for k, v in neutral.items() if keys is None or k in keys}
    return desc


def test_resolve_mouth_set_chain():
    desc = _with_variant(CharacterDescriptor(name="m"), "happy")
    assert resolve_mouth_set(desc, "happy", keys_used=["A", "D", "X"]) == "viseme@happy"
    assert resolve_mouth_set(desc, "amused", keys_used=["A"]) == "viseme@happy"
    assert resolve_mouth_set(desc, "thinking", keys_used=["A"]) == "viseme"
    assert resolve_mouth_set(desc, None, keys_used=["A"]) == "viseme"
    # Declared but not covering the line's keys: neutral, with the keys named.
    partial = _with_variant(CharacterDescriptor(name="p"), "sad", keys={"A", "X"})
    with pytest.warns(UserWarning, match=r"\['D'\]"):
        assert resolve_mouth_set(partial, "sad", keys_used=["A", "D"]) == "viseme"
    # Nothing to fall back on: an error naming the character.
    mute = CharacterDescriptor(name="q")
    del mute.asset_sets["viseme"]
    with pytest.raises(ExpressionResolutionError, match="'q'"):
        resolve_mouth_set(mute, "happy", keys_used=["A"])
    with pytest.raises(ExpressionResolutionError, match="cannot speak"):
        resolve_mouth_set(mute, None, keys_used=["A"])


def test_expression_problems_name_every_exit():
    desc = CharacterDescriptor(name="m")
    assert expression_problems(desc, preset="happy", who="m") == []
    assert expression_problems(desc, preset="happy", axes=["gaze_x"], who="m") == []
    (p,) = expression_problems(desc, preset="joyful", who="m")
    assert "joyful" in p and "known:" in p
    (p,) = expression_problems(desc, preset=None, axes=["nose"], who="m")
    assert "nose" in p
    baked = CharacterDescriptor(name="b", face_overlay=False)
    (p,) = expression_problems(baked, preset="happy", who="b")
    assert "promote" in p and "add-gaze" in p


# ---------------------------------------------------------------- provider


def _shot(actions=(), dialogue=(), duration=2.0):
    return Shot(
        id="s", style="cutout", duration=duration,
        entities=[AssetRef(kind="character", id="c", store="characters", ref="c")],
        actions=list(actions), dialogue=list(dialogue),
    )


def test_spans_come_from_leaves_and_dialogue_sugar_and_never_touch_the_shot():
    shot = _shot(
        actions=[sequence(delay(0.5), expression("c", "angry", duration=1.0))],
        dialogue=[Dialogue(speaker="c", text="hi", emotion="happy", start=1.2, duration=0.5)],
    )
    spans = expression_spans(shot, "c")
    assert [(s.preset, s.start, s.end, s.source) for s in spans] == [
        ("angry", 0.5, 1.5, "action"), ("happy", 1.2, 1.7, "dialogue"),
    ]
    assert spans[1].blend == DIALOGUE_EMOTION_BLEND_S
    # In memory only: the shot's actions still hold exactly what was authored.
    assert len(shot.actions) == 1 and shot.actions[0].kind == "sequence"
    assert expression_spans(shot, "someone-else") == []


def test_duration_none_runs_to_the_shot_end_and_is_zero_width_in_a_sequence():
    shot = _shot(actions=[sequence(expression("c", "sad"), delay(1.0), expression("c", "happy"))])
    spans = expression_spans(shot, "c")
    assert [(s.preset, s.start, s.end) for s in spans] == [("sad", 0.0, 2.0), ("happy", 1.0, 2.0)]


def test_curves_are_summed_ramped_and_frame_aligned():
    shot = _shot(actions=[expression("c", "angry", duration=1.0, blend=0.5), expression("c", "surprised", duration=2.0, blend=0.0)])
    curves = {c.axis: c.samples for c in DefaultExpressionProvider().curves(shot, "c", fps=4)}
    assert len(curves["brow_height_l"]) == 9  # frames 0..8 of a 2 s shot at 4 fps
    # surprised (+1.0) all along; angry (-0.6) ramping 0 → 1 over 0.5 s then back down.
    assert curves["brow_height_l"][0] == pytest.approx(1.0)  # ramp weight 0 at the start
    assert curves["brow_height_l"][2] == pytest.approx(1.0 - 0.6)  # t=0.5: angry fully in
    assert curves["brow_height_l"][4] == pytest.approx(1.0)  # t=1.0: angry ramped out
    assert curves["brow_height_l"][8] == pytest.approx(1.0)


def test_the_sum_is_order_independent():
    a = expression("c", "angry", duration=1.0)
    b = expression("c", "happy", duration=1.5, blend=0.2)
    one = {c.axis: c.samples for c in DefaultExpressionProvider().curves(_shot(actions=[a, b]), "c", fps=12)}
    two = {c.axis: c.samples for c in DefaultExpressionProvider().curves(_shot(actions=[b, a]), "c", fps=12)}
    assert one == two


def test_mouth_preset_at_is_whole_line_and_prefers_the_heaviest():
    shot = _shot(actions=[expression("c", "sad", duration=2.0, intensity=0.4), expression("c", "happy", duration=1.0, intensity=0.9)])
    p = DefaultExpressionProvider()
    assert p.mouth_preset_at(shot, "c", 0.5) == "happy"
    assert p.mouth_preset_at(shot, "c", 1.5) == "sad"
    assert p.mouth_preset_at(_shot(actions=[expression("c", "thinking")]), "c", 0.5) is None


# ---------------------------------------------------------------- blendshapes


def test_the_vocabulary_is_the_cards_52_and_no_identifier_says_arkit():
    assert len(BLENDSHAPE_V2_NAMES) == 52 and len(set(BLENDSHAPE_V2_NAMES)) == 52
    import an.expression.blendshapes as mod

    assert "arkit" not in "".join(dir(mod)).lower()
    assert from_blendshapes({"eyeBlinkRight": 1.0, "browOuterUpLeft": 0.5}) == {
        "lid_open_r": -1.0, "brow_height_l": 0.5, "brow_angle_l": -0.5,
    }
    with pytest.raises(ValueError, match="eyeBlinkMiddle"):
        from_blendshapes({"eyeBlinkMiddle": 1.0})
