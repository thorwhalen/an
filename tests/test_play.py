"""`play` resolves a descriptor animation into channels (an#7).

`PlayAction` used to be refused at three layers (markdown parse, validate,
compile) because named animations had nowhere to be looked up from — while
every descriptor carried seeded `idle_breath` and `blink` animations nothing
consumed. Resolution: `target` names the entity → its migrated descriptor →
`animations[name]` → tracks converted to channels on the entity's nodes.

Two conversions the naive version got wrong, both pinned here: bone tracks
are DEVIATIONS in view-box units (rotation in degrees) around the bone's
rest, and channels carry ABSOLUTE scene values (radians); slot tracks name
ATTACHMENTS, and swap channels carry set KEYS.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

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
        style="cutout",
        duration=duration,
        entities=[AssetRef(kind="character", id="gale", store="characters", ref="gale")],
        actions=list(actions),
    )


def _play_clips(scene):
    return {aid: a for aid, a in scene.animations.items() if aid.startswith("__play__")}


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
        style="cutout",
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
    from tests.test_swap_channels import _evaluate, _python_timeline

    scene = compile_shot(
        _shot([sequence(delay(1.0), play("gale", "blink"))], duration=3.0),
        mall={"characters": gale_store},
    )
    tl = _python_timeline(scene)
    key = ("gale/head/left_eye", "eyelid")
    assert _evaluate(tl, 1.0)[key] == "OPEN"
    assert _evaluate(tl, 1.05)[key] == "CLOSED"
    assert _evaluate(tl, 1.17)[key] == "OPEN"
