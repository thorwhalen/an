"""`play` resolves a descriptor animation into channels (an#7).

`PlayAction` used to be refused at three layers (markdown parse, validate,
compile) because named animations had nowhere to be looked up from — while
every descriptor carried seeded `idle_breath` and `blink` animations nothing
consumed. Resolution: `target` names the entity → its migrated descriptor →
`animations[name]` → tracks converted to channels on the entity's nodes.

Two conversions a naive copy gets wrong, both pinned here: bone tracks are
DEVIATIONS in view-box units (rotation in degrees) around the bone's rest,
and channels carry ABSOLUTE scene values (radians); slot tracks name
ATTACHMENTS, and swap channels carry set KEYS.

Resolution itself lives in `an.characters.play`, shared by validate and the
compiler; the `_refusals` cases below assert BOTH surfaces on every way a
play can fail to resolve, because the compiler used to decide alone and
validate passed plays it then refused (an#7 review).
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

from an.adapters.cutout.timeline import evaluate_timeline
import pytest

from an.adapters.cutout.compile import SCENE_PX_PER_VIEW_BOX, CutoutCompileError, compile_shot
from an.ir.compose import delay, play, sequence
from an.ir.schema import AssetRef, PlayAction, Shot
from an.stores.characters import CharactersStore

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "characters"


@pytest.fixture()
def gale_store(tmp_path):
    shutil.copytree(FIXTURES / "gale", tmp_path / "gale")
    return CharactersStore(tmp_path)


def _shot(actions, duration=6.0):
    return Shot(
        id="s",
        renderer="cutout",
        duration=duration,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
        actions=list(actions),
    )


def _play_clips(scene):
    return {aid: a for aid, a in scene.animations.items() if aid.startswith("__play__")}


def _edit(store, fn):
    """Apply ``fn`` to gale's stored descriptor dict and write it back."""
    d = dict(store["gale"])
    fn(d)
    store["gale"] = d


def _add_animation(d, name, duration, tracks, *, loop=False):
    d.setdefault("animations", {})[name] = {
        "name": name,
        "duration": duration,
        "loop": loop,
        "tracks": tracks,
    }


def _timeline(scene):
    from an.adapters.cutout.timeline import timeline_from_scene

    return timeline_from_scene(scene)


def _rest(scene, slot):
    """The built rest transform of gale's ``slot`` node (top-level slots)."""
    return next(c for c in scene.scene.children[0].children if c.name == slot).transform


K = SCENE_PX_PER_VIEW_BOX / 1024


def test_idle_breath_resolves_to_channels_around_the_rest_pose(gale_store):
    scene = compile_shot(_shot([play("gale", "idle_breath")]), mall={"characters": gale_store}, fps=24)
    (clip,) = _play_clips(scene).values()
    assert clip.loop_mode == "loop"  # the descriptor's own loop=True
    assert clip.duration == pytest.approx(6.0)  # max(breath 4 s, weight shift 6 s)
    by_key = {(c.target, c.property): c for c in clip.channels}
    assert set(by_key) == {("gale/torso", "y"), ("gale/head", "rotation"), ("gale/torso", "x")}
    # Deviation around REST, scaled by the rig's k: the torso bobs ±2 view-box
    # units about its built y, not about zero.
    torso = next(c for c in scene.scene.children[0].children if c.name == "torso")
    k = SCENE_PX_PER_VIEW_BOX / 1024
    ys = [kf.value for kf in by_key[("gale/torso", "y")].keyframes]
    assert min(ys) == pytest.approx(torso.transform.y - 2.0 * k, abs=1e-6)
    assert max(ys) == pytest.approx(torso.transform.y + 2.0 * k, abs=1e-6)
    # Degrees in the descriptor, radians on the wire: ±0.5° about the rest.
    rots = [kf.value for kf in by_key[("gale/head", "rotation")].keyframes]
    assert max(abs(r) for r in rots) == pytest.approx(math.radians(0.5), abs=1e-9)
    # Sampled at the frame rate, linear between samples.
    assert all(kf.easing == "linear" for kf in by_key[("gale/torso", "y")].keyframes)
    assert len(by_key[("gale/torso", "y")].keyframes) == 6 * 24 + 1


def test_blink_resolves_to_eyelid_swap_channels_by_key(gale_store):
    scene = compile_shot(_shot([play("gale", "blink")]), mall={"characters": gale_store})
    (clip,) = _play_clips(scene).values()
    assert clip.loop_mode == "once"
    assert clip.duration == pytest.approx(0.18)
    by_key = {(c.target, c.property): c for c in clip.channels}
    assert set(by_key) == {("gale/head/left_eye", "eyelid"), ("gale/head/right_eye", "eyelid")}
    kfs = by_key[("gale/head/left_eye", "eyelid")].keyframes
    # The descriptor's frames name ATTACHMENTS (open/closed); the channel
    # carries the set's KEYS, resolved through the node's projection.
    assert [(round(k.time, 3), k.value) for k in kfs] == [
        (0.0, "OPEN"),
        (0.025, "CLOSED"),
        (0.155, "OPEN"),
    ]
    assert all(k.easing == "step" for k in kfs)


def test_loop_is_the_action_s_override_or_the_animation_s_own(gale_store):
    scene = compile_shot(
        _shot(
            [
                play("gale", "idle_breath", duration=2.0, loop=False),
                play("gale", "blink", duration=2.0, loop=True),
                play("gale", "blink"),
            ]
        ),
        mall={"characters": gale_store},
    )
    clips = _play_clips(scene)
    modes = sorted((aid, c.loop_mode) for aid, c in clips.items())
    assert modes == [("__play__0", "once"), ("__play__1", "loop"), ("__play__2", "once")]
    # Per-INSTANCE clips: three plays, three clips, three placements.
    placed = [p for t in scene.timeline.tracks for p in t.clips if p.animation_id.startswith("__play__")]
    assert len(placed) == 3
    assert placed[0].duration == 2.0 and placed[2].duration is None  # natural


def test_an_undeclared_animation_is_refused_naming_the_declared_ones(gale_store):
    with pytest.raises(CutoutCompileError, match=r"walk.*blink.*idle_breath|idle_breath.*walk"):
        compile_shot(_shot([play("gale", "walk")]), mall={"characters": gale_store})


def test_a_procedural_entity_cannot_play_anything():
    shot = Shot(
        id="s",
        renderer="cutout",
        duration=2.0,
        entities=[AssetRef(kind="character", id="c", store="characters", ref="c")],
        actions=[play("c", "idle_breath")],
    )
    with pytest.raises(CutoutCompileError, match="no descriptor"):
        compile_shot(shot, mall={"characters": {}})


def test_play_round_trips_through_scene_md(gale_store):
    from an.ir.schema import Meta, SceneIR
    from an.ir.sync import ir_to_markdown, markdown_to_ir

    scene = SceneIR(
        meta=Meta(title="t", duration=6.0),
        timeline=[
            _shot([sequence(delay(1.0), play("gale", "idle_breath", duration=3.0, loop=False))])
        ],
    )
    md = ir_to_markdown(scene)
    assert "kind: play" in md
    back = markdown_to_ir(md)
    (leaf,) = [f.action for f in __import__("an.ir.compose", fromlist=["flatten"]).flatten(back.timeline[0].actions[0])]
    assert isinstance(leaf, PlayAction)
    assert (leaf.animation, leaf.duration, leaf.loop) == ("idle_breath", 3.0, False)
    # Compiles: the round-tripped play resolves like the authored one.
    compile_shot(back.timeline[0], mall={"characters": gale_store})


def test_validate_refuses_an_undeclared_animation_before_compile(gale_store):
    from an.ir.schema import Meta, SceneIR
    from an.ir.validate import validate_semantic

    bad = SceneIR(meta=Meta(title="t", duration=2.0), timeline=[_shot([play("gale", "walk")])])
    report = validate_semantic(bad, available_characters=gale_store)
    assert not report.passed
    assert any("walk" in f.description and "blink" in f.description for f in report.findings)
    good = SceneIR(meta=Meta(title="t", duration=2.0), timeline=[_shot([play("gale", "blink")])])
    assert validate_semantic(good, available_characters=gale_store).passed


def test_a_play_layers_over_the_compiled_blink_by_track_order(gale_store):
    """A played `blink` sits after the compiled blink clips on the track, so
    it wins during its window — the eyes it opens/closes are the author's."""
    from an.adapters.cutout.timeline import timeline_from_scene

    scene = compile_shot(
        _shot([sequence(delay(1.0), play("gale", "blink"))], duration=3.0),
        mall={"characters": gale_store},
    )
    tl = timeline_from_scene(scene)
    key = ("gale/head/left_eye", "eyelid")
    assert evaluate_timeline(tl, 1.0)[key] == "OPEN"
    assert evaluate_timeline(tl, 1.05)[key] == "CLOSED"
    assert evaluate_timeline(tl, 1.17)[key] == "OPEN"


# ------------------------------------------------ the samples, not the envelope


def test_phase_and_rest_hold_at_a_specific_sample(gale_store):
    """Min/max over a cycle cannot see phase; a sample can. The head tilt is
    documented as lagging the chest by 0.25 cycles — at t=0 that is the PEAK
    (sin(2π·0.25) = 1) while the torso sits exactly at rest."""
    scene = compile_shot(_shot([play("gale", "idle_breath")]), mall={"characters": gale_store}, fps=24)
    (clip,) = _play_clips(scene).values()
    by_key = {(c.target, c.property): c for c in clip.channels}
    head_rot0 = by_key[("gale/head", "rotation")].keyframes[0]
    torso_y0 = by_key[("gale/torso", "y")].keyframes[0]
    torso_x0 = by_key[("gale/torso", "x")].keyframes[0]
    assert head_rot0.time == 0.0 and head_rot0.value == pytest.approx(math.radians(0.5), abs=1e-12)
    assert torso_y0.value == pytest.approx(_rest(scene, "torso").y, abs=1e-12)
    assert torso_x0.value == pytest.approx(_rest(scene, "torso").x, abs=1e-12)


def test_step_and_linear_bone_tracks_are_deviations_around_rest_too(gale_store):
    """Only the sine path was pinned; a hand-authored frame track that dropped
    the `rest +` would teleport the node to the origin (surviving mutant)."""
    _edit(
        gale_store,
        lambda d: _add_animation(
            d,
            "nod",
            1.0,
            [
                {"target": "bone:head.y", "type": "linear", "frames": [[0.0, 0.0], [1.0, 10.0]]},
                {"target": "bone:torso.rotation_deg", "type": "step", "frames": [[0.0, 0.0], [0.5, 90.0]]},
            ],
        ),
    )
    scene = compile_shot(_shot([play("gale", "nod")]), mall={"characters": gale_store})
    (clip,) = _play_clips(scene).values()
    by_key = {(c.target, c.property): c for c in clip.channels}
    head_y = by_key[("gale/head", "y")].keyframes
    assert [k.easing for k in head_y] == ["linear", "linear"]
    assert head_y[0].value == pytest.approx(_rest(scene, "head").y)
    assert head_y[1].value == pytest.approx(_rest(scene, "head").y + 10.0 * K)
    rot = by_key[("gale/torso", "rotation")].keyframes
    assert [k.easing for k in rot] == ["step", "step"]
    assert rot[1].value == pytest.approx(_rest(scene, "torso").rotation + math.pi / 2)


def test_a_sine_track_always_closes_its_cycle_at_the_clip_end(gale_store):
    """0.19 s at 24 fps: `round` gave 4 intervals ending at 0.1667 s and held
    that value to the clip end, so a loop wrapped with a jump. `ceil` puts the
    last sample AT the duration, where the cycle closes on its first value."""
    _edit(
        gale_store,
        lambda d: _add_animation(
            d, "wobble", 0.19,
            [{"target": "bone:head.rotation_deg", "type": "sine", "amplitude": 10.0}],
        ),
    )
    scene = compile_shot(_shot([play("gale", "wobble")]), mall={"characters": gale_store}, fps=24)
    (clip,) = _play_clips(scene).values()
    (ch,) = clip.channels
    times = [k.time for k in ch.keyframes]
    assert times[-1] == pytest.approx(0.19)
    assert times[-2] == pytest.approx(4 / 24)
    assert ch.keyframes[-1].value == pytest.approx(ch.keyframes[0].value, abs=1e-9)


def test_position_tracks_scale_by_the_view_box_height_not_width(gale_store):
    """`k` is derived from view_box[3]; a square fixture cannot tell."""
    _edit(gale_store, lambda d: d.update(view_box=[0, 0, 1024, 2048]))
    scene = compile_shot(_shot([play("gale", "idle_breath")]), mall={"characters": gale_store})
    (clip,) = _play_clips(scene).values()
    ys = [k.value for c in clip.channels if c.target == "gale/torso" and c.property == "y" for k in c.keyframes]
    k_tall = SCENE_PX_PER_VIEW_BOX / 2048
    assert max(ys) - min(ys) == pytest.approx(4.0 * k_tall, abs=1e-6)


def test_speed_scales_the_played_clip(gale_store):
    """`speed=2` halves the window and doubles the rate: the torso peaks at
    0.75 s (a quarter of the 6 s cycle, at double speed)."""
    scene = compile_shot(_shot([play("gale", "idle_breath", speed=2.0)]), mall={"characters": gale_store}, fps=24)
    placed = next(p for t in scene.timeline.tracks for p in t.clips if p.animation_id.startswith("__play__"))
    assert placed.speed == 2.0
    pose = evaluate_timeline(_timeline(scene), 0.75)
    assert pose[("gale/torso", "y")] == pytest.approx(_rest(scene, "torso").y + 2.0 * K, abs=1e-6)


# -------------------------------------------------------- loops, on the timeline


def test_a_looping_play_without_duration_runs_to_the_shot_end(gale_store):
    """The defect one layer up from #7's title: `play("gale", "idle_breath")`
    placed itself for ONE natural cycle, so `loop=True` was a no-op unless a
    `duration` was also given. A loop with nothing to bound it keeps going."""
    scene = compile_shot(_shot([play("gale", "idle_breath")], duration=12.0), mall={"characters": gale_store}, fps=24)
    placed = next(p for t in scene.timeline.tracks for p in t.clips if p.animation_id.startswith("__play__"))
    assert placed.duration == pytest.approx(12.0)
    tl = _timeline(scene)
    rest_y = _rest(scene, "torso").y
    # 7.5 s = 1.5 s into the SECOND cycle: the same peak as 1.5 s.
    assert evaluate_timeline(tl, 7.5)[("gale/torso", "y")] == pytest.approx(rest_y + 2.0 * K, abs=1e-6)
    assert evaluate_timeline(tl, 1.5)[("gale/torso", "y")] == pytest.approx(rest_y + 2.0 * K, abs=1e-6)
    # And the widening is by `speed`, so the window still lands on the shot end.
    fast = compile_shot(_shot([play("gale", "idle_breath", speed=2.0)], duration=12.0), mall={"characters": gale_store})
    placed = next(p for t in fast.timeline.tracks for p in t.clips if p.animation_id.startswith("__play__"))
    assert placed.duration == pytest.approx(24.0)
    assert ("gale/torso", "y") in evaluate_timeline(_timeline(fast), 11.9)


def test_a_non_looping_play_keeps_its_natural_window(gale_store):
    scene = compile_shot(_shot([play("gale", "blink")], duration=12.0), mall={"characters": gale_store})
    placed = next(p for t in scene.timeline.tracks for p in t.clips if p.animation_id.startswith("__play__"))
    assert placed.duration is None
    assert evaluate_timeline(_timeline(scene), 6.0).get(("gale/head/left_eye", "eyelid"), "OPEN") == "OPEN"


def test_a_widened_play_loops_at_its_natural_period(gale_store):
    """`duration=12` widens the WINDOW; the clip still loops against its own
    6 s — the mutant that stretched the clip to 12 s gave one slow cycle."""
    scene = compile_shot(_shot([play("gale", "idle_breath", duration=12.0)], duration=12.0), mall={"characters": gale_store}, fps=24)
    tl = _timeline(scene)
    y = ("gale/torso", "y")
    assert evaluate_timeline(tl, 7.5)[y] == pytest.approx(evaluate_timeline(tl, 1.5)[y], abs=1e-6)
    assert evaluate_timeline(tl, 7.5)[y] != pytest.approx(_rest(scene, "torso").y, abs=1e-3)


# ------------------------------------------------------- one set per slot track


def test_a_slot_track_resolves_to_one_set_never_split_across_two(gale_store):
    """Per-frame resolution split `blink` across `aa` (only `closed`) and
    `eyelid` (both); the runtime applies pose keys in name order, `aa` before
    `eyelid`, so OPEN landed last on every frame and the blink never closed.
    The set that names EVERY frame's attachment is the track's set."""
    _edit(gale_store, lambda d: d["asset_sets"].update(aa={"S": "closed"}))
    scene = compile_shot(
        _shot([sequence(delay(1.0), play("gale", "blink"))], duration=3.0),
        mall={"characters": gale_store},
    )
    (clip,) = _play_clips(scene).values()
    left = [c for c in clip.channels if c.target == "gale/head/left_eye"]
    assert [c.property for c in left] == ["eyelid"]
    assert evaluate_timeline(_timeline(scene), 1.05)[("gale/head/left_eye", "eyelid")] == "CLOSED"


def test_two_sets_covering_a_track_is_an_error_naming_both(gale_store):
    _edit(gale_store, lambda d: d["asset_sets"].update(lids={"UP": "open", "DOWN": "closed"}))
    with pytest.raises(CutoutCompileError, match=r"eyelid.*lids|lids.*eyelid"):
        compile_shot(_shot([play("gale", "blink")]), mall={"characters": gale_store})


# ---------------------------------------------------------- node addressing


def test_bone_root_animates_the_entity_container(gale_store):
    _edit(
        gale_store,
        lambda d: _add_animation(
            d, "slide", 1.0,
            [{"target": "bone:root.x", "type": "linear", "frames": [[0.0, 0.0], [1.0, 100.0]]}],
        ),
    )
    scene = compile_shot(_shot([play("gale", "slide")]), mall={"characters": gale_store})
    (clip,) = _play_clips(scene).values()
    (ch,) = clip.channels
    assert (ch.target, ch.property) == ("gale", "x")
    assert ch.keyframes[-1].value == pytest.approx(100.0 * K)


def test_a_play_targets_its_own_entity_when_two_share_a_rig(gale_store):
    shot = Shot(
        id="s",
        renderer="cutout",
        duration=2.0,
        entities=[
            AssetRef(kind="character", id="abel", store="characters", ref="gale"),
            AssetRef(kind="character", id="gale", store="characters", ref="gale"),
        ],
        actions=[play("gale", "blink")],
    )
    scene = compile_shot(shot, mall={"characters": gale_store})
    (clip,) = _play_clips(scene).values()
    assert {c.target.split("/", 1)[0] for c in clip.channels} == {"gale"}


# --------------------------------------- every refusal, on BOTH surfaces


def _delete_closed_eye(store):
    import os

    os.remove(Path(store._root) / "gale" / "parts" / "eye_l_closed.svg")


def _bone_without_a_primary_slot(d):
    # gale's `hand_l` bone owns the `left_hand` slot, not one named `hand_l`.
    _add_animation(d, "wave", 1.0, [{"target": "bone:hand_l.rotation_deg", "type": "sine", "amplitude": 5.0}])


def _unknown_bone_property(d):
    _add_animation(d, "fade", 1.0, [{"target": "bone:head.alpha", "type": "linear", "frames": [[0.0, 1.0], [1.0, 0.0]]}])


def _undeclared_bone(d):
    _add_animation(d, "tail", 1.0, [{"target": "bone:tail.rotation_deg", "type": "sine", "amplitude": 5.0}])


def _attachment_not_in_skin(d):
    _add_animation(d, "wink", 0.2, [{"target": "slot:left_eye.attachment", "type": "step", "frames": [[0.0, "open"], [0.1, "half"]]}])


def _no_set_names_it(d):
    # `torso_left` is IN the skin but only `body_facing` names it — drop the set.
    del d["asset_sets"]["body_facing"]
    _add_animation(d, "turn", 1.0, [{"target": "slot:torso.attachment", "type": "step", "frames": [[0.0, "torso"], [0.5, "torso_left"]]}])


def _boolean_bone_frame(d):
    _add_animation(d, "flag", 1.0, [{"target": "bone:head.y", "type": "step", "frames": [[0.0, True]]}])


_REFUSALS = {
    "undeclared animation": (None, "walk", ["walk", "blink", "idle_breath"]),
    "bone without a primary slot": (_bone_without_a_primary_slot, "wave", ["hand_l", "primary slot", "left_hand"]),
    "unknown bone property": (_unknown_bone_property, "fade", ["alpha", "rotation_deg"]),
    "undeclared bone": (_undeclared_bone, "tail", ["tail", "declares no bone"]),
    "attachment the skin lacks": (_attachment_not_in_skin, "wink", ["half", "does not carry"]),
    "attachment no set names": (_no_set_names_it, "turn", ["torso_left", "no declared asset set"]),
    "two sets cover the track": (lambda d: d["asset_sets"].update(lids={"UP": "open", "DOWN": "closed"}), "blink", ["eyelid", "lids", "exactly one set"]),
    "boolean bone frame": (_boolean_bone_frame, "flag", ["True", "must be numbers"]),
    "face slot suppressed by face_overlay": (lambda d: d.update(face_overlay=False), "blink", ["left_eye", "face_overlay"]),
    "art missing for a frame": ("delete-art", "blink", ["eye_l_closed.svg", "not on disk"]),
}


@pytest.mark.parametrize("case", sorted(_REFUSALS))
def test_validate_and_compile_refuse_the_same_plays_with_the_same_words(gale_store, case):
    """The gate that runs before TTS/Chromium spend must have the compiler's
    verdict. Before the shared resolver, four of these passed `an validate`
    and raised at compile (an#7 review)."""
    from an.ir.schema import Meta, SceneIR
    from an.ir.validate import validate_semantic

    edit, name, words = _REFUSALS[case]
    if edit == "delete-art":
        _delete_closed_eye(gale_store)
    elif edit is not None:
        _edit(gale_store, edit)
    scene = SceneIR(meta=Meta(title="t", duration=2.0), timeline=[_shot([play("gale", name)])])
    report = validate_semantic(scene, available_characters=gale_store)
    assert not report.passed, case
    errors = [f.description for f in report.findings if f.severity == "error"]
    for word in words:
        assert any(word in e for e in errors), (case, word, errors)
    with pytest.raises(CutoutCompileError) as e:
        compile_shot(scene.timeline[0], mall={"characters": gale_store})
    for word in words:
        assert word in str(e.value), (case, word, str(e.value))


def test_validate_sees_a_play_wrapped_by_start(gale_store):
    """The documented `start:` idiom nests the play in a sequence; a gate
    that only walked the top level let it through (surviving mutant)."""
    from an.ir.schema import Meta, SceneIR
    from an.ir.validate import validate_semantic

    scene = SceneIR(
        meta=Meta(title="t", duration=2.0),
        timeline=[_shot([sequence(delay(1.0), play("gale", "walk"))])],
    )
    report = validate_semantic(scene, available_characters=gale_store)
    assert any("walk" in f.description for f in report.findings if f.severity == "error")


def test_scene_md_play_without_loop_keeps_the_descriptor_s_own(gale_store):
    """A reader that defaulted `loop` to False would silently un-loop
    `idle_breath` on every round trip (surviving mutant)."""
    from an.ir.schema import Meta, SceneIR
    from an.ir.sync import ir_to_markdown, markdown_to_ir
    from an.ir.compose import flatten

    scene = SceneIR(meta=Meta(title="t", duration=6.0), timeline=[_shot([play("gale", "idle_breath")])])
    md = ir_to_markdown(scene)
    assert "loop" not in md.split("```yaml actions", 1)[1].split("```", 1)[0]
    (leaf,) = [f.action for f in flatten(markdown_to_ir(md).timeline[0].actions[0])]
    assert leaf.loop is None
    compiled = compile_shot(markdown_to_ir(md).timeline[0], mall={"characters": gale_store})
    (clip,) = _play_clips(compiled).values()
    assert clip.loop_mode == "loop"
