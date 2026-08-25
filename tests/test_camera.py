"""The camera translates (an#109).

`root.pivot` was already a 2D camera and already runtime-applied — PixiJS
composes ``world = position + M·(local − pivot)`` and the runtime indexes the
centre container as `"root"`. So a translating camera is a **compiler** change
with zero runtime change, which is why these tests run on every PR rather than
only on a labelled one, and why the pixels needed no new machinery.

The acceptance that matters is the negative one: a shot that does not
translate must compile **byte-identically** to what it compiled to before this
existed. Five named zoom moves, eight corpus scenes, no exemption.
"""

from __future__ import annotations

import pytest

from an.adapters.cutout.compile import (
    CAMERA_MOVES,
    CutoutCompileError,
    PAN_FRACTION,
    camera_keys,
    compile_shot,
)
from an.ir.schema import Camera, CameraKey, Meta, SceneIR, SetAction, Shot
from an.ir.validate import validate_semantic


def _shot(camera=None, *, duration=2.0, actions=()) -> Shot:
    return Shot(
        id="s1",
        renderer="cutout",
        duration=duration,
        camera=camera,
        actions=list(actions),
    )


def _channels(scene) -> dict[str, list[tuple[float, float, str | None]]]:
    """`{property: [(time, value, easing)]}` for every camera channel."""
    out = {}
    for aid, anim in scene.animations.items():
        if not aid.startswith("__camera__"):
            continue
        for ch in anim.channels:
            out[ch.property] = [(k.time, k.value, k.easing) for k in ch.keyframes]
    return out


def _validate(shot):
    return validate_semantic(SceneIR(meta=Meta(), timeline=[shot]))


# --- the byte-identity acceptance -------------------------------------------


@pytest.mark.parametrize(
    "move,end",
    [("push_in", 1.25), ("pull_out", 0.8), ("zoom_in", 1.5), ("zoom_out", 0.7)],
)
def test_a_zoom_move_emits_exactly_the_document_it_always_did(move, end):
    """Not "a scale tween" — THIS document.

    Two animations named `__camera__<shot>_scale_x`/`_scale_y`, one channel
    each targeting `root`, keyframes `[(0, 1.0, "ease_in_out"), (d, s, null)]`,
    on two tracks rooted at `"__camera__"`, and **no pivot channels**. The
    emitter emits only properties that actually vary, which is what makes that
    true rather than merely intended — a per-key easing default, or emitting
    every channel unconditionally, moves every camera scene's contract hash.
    """
    scene = compile_shot(_shot(Camera(move=move)), fps=24, width=320, height=240)
    assert _channels(scene) == {
        "scale_x": [(0.0, 1.0, "ease_in_out"), (2.0, end, None)],
        "scale_y": [(0.0, 1.0, "ease_in_out"), (2.0, end, None)],
    }
    assert sorted(scene.animations) == [
        "__camera__s1_scale_x",
        "__camera__s1_scale_y",
    ]
    assert [t.target_root for t in scene.timeline.tracks] == ["__camera__"] * 2


def test_hold_and_no_camera_emit_nothing_at_all():
    """`hold` is a real no-op, not an unknown move — the one camera value that
    has always worked."""
    for camera in (None, Camera(), Camera(move="hold"), Camera(move="")):
        scene = compile_shot(_shot(camera), fps=24, width=320, height=240)
        assert scene.animations == {}, camera
        assert scene.timeline.tracks == [], camera


# --- what is new: translation ------------------------------------------------


@pytest.mark.parametrize(
    "move,prop,sign,span",
    [
        ("pan_left", "pivot_x", -1, 320),
        ("pan_right", "pivot_x", 1, 320),
        ("tilt_up", "pivot_y", -1, 240),
        ("tilt_down", "pivot_y", 1, 240),
    ],
)
def test_a_pan_moves_the_pivot_and_touches_no_scale(move, prop, sign, span):
    """`+x` moves the CAMERA right, so `pan_left` ends at negative x — and a
    pan emits no scale channel, so it cannot accidentally zoom."""
    scene = compile_shot(_shot(Camera(move=move)), fps=24, width=320, height=240)
    channels = _channels(scene)
    assert set(channels) == {prop}
    (t0, v0, e0), (t1, v1, e1) = channels[prop]
    assert (t0, v0, e0) == (0.0, 0.0, "ease_in_out")
    assert (t1, e1) == (2.0, None)
    assert v1 == pytest.approx(sign * span * PAN_FRACTION)


def test_a_tilt_scales_by_the_HEIGHT_not_the_width():
    """A frame is wider than it is tall, so one span for both axes makes a tilt
    travel further than the picture it is tilting across."""
    wide = compile_shot(_shot(Camera(move="tilt_down")), fps=24, width=1920, height=240)
    assert _channels(wide)["pivot_y"][1][1] == pytest.approx(240 * PAN_FRACTION)


def test_explicit_keys_are_the_same_path_as_a_named_move():
    """One code path, two front doors. A named move IS a key list."""
    keys = camera_keys(_shot(Camera(move="push_in")), width=320, height=240)
    by_keys = compile_shot(_shot(Camera(keys=keys)), fps=24, width=320, height=240)
    by_name = compile_shot(_shot(Camera(move="push_in")), fps=24, width=320, height=240)
    assert by_keys.model_dump_json() == by_name.model_dump_json()


def test_keys_can_pan_and_zoom_at_once_and_the_zoom_goes_through_the_pivot():
    """Pixi scales about the pivot, so a push-in during a pan zooms toward what
    the camera is looking at rather than toward a fixed frame centre. That is
    the correct default and it costs nothing — both are channels on `root`."""
    scene = compile_shot(
        _shot(Camera(keys=[CameraKey(at=0.0), CameraKey(at=2.0, x=-100.0, zoom=1.5)])),
        fps=24,
        width=320,
        height=240,
    )
    assert set(_channels(scene)) == {"pivot_x", "scale_x", "scale_y"}


# --- refusals, never no-ops --------------------------------------------------


def test_setting_both_doors_raises_at_compile_and_errors_at_validate():
    shot = _shot(Camera(move="push_in", keys=[CameraKey(at=0.0), CameraKey(at=1.0)]))
    with pytest.raises(CutoutCompileError, match="BOTH"):
        compile_shot(shot, fps=24, width=320, height=240)
    assert not _validate(shot).passed


def test_an_unknown_move_raises_and_lists_what_exists():
    with pytest.raises(CutoutCompileError, match="whip_pan") as e:
        compile_shot(_shot(Camera(move="whip_pan")), fps=24, width=320, height=240)
    assert "pan_left" in str(e.value)
    assert not _validate(_shot(Camera(move="whip_pan"))).passed


def test_the_two_move_vocabularies_are_the_same_set():
    """validate predicts what compile does. Two hand-maintained tables is how
    they drift, and the drift is silent in the direction that matters — a move
    that validates and then raises, after the author has paid for TTS and a
    browser launch."""
    from an.ir.validate import _RENDERABLE_CAMERA_MOVES

    assert set(_RENDERABLE_CAMERA_MOVES) == set(CAMERA_MOVES)


@pytest.mark.parametrize(
    "keys,needle",
    [
        ([CameraKey(at=2.0), CameraKey(at=0.0)], "time order"),
        ([CameraKey(at=0.0), CameraKey(at=99.0)], "outside the shot"),
        ([CameraKey(at=-1.0), CameraKey(at=1.0)], "outside the shot"),
    ],
)
def test_validate_refuses_keys_that_cannot_play(keys, needle):
    report = _validate(_shot(Camera(keys=keys)))
    assert not report.passed
    assert any(needle in f.description for f in report.findings), [
        f.description for f in report.findings
    ]


def test_a_single_key_is_a_warning_not_an_error():
    """A pose, not a move: it renders as if the camera were absent. Worth
    saying — the author wrote a camera block that does nothing — and not worth
    failing, because nothing breaks."""
    report = _validate(_shot(Camera(keys=[CameraKey(at=0.0, x=50.0)])))
    assert report.passed
    assert any("single camera key" in f.description for f in report.findings)


def test_a_zero_or_negative_zoom_is_refused_by_the_schema():
    """A camera with no magnification is not a camera, and the compiler would
    emit a degenerate root scale that collapses the whole frame."""
    import pydantic

    for zoom in (0.0, -1.0, float("inf")):
        with pytest.raises(pydantic.ValidationError):
            CameraKey(zoom=zoom)


# --- the collision rule ------------------------------------------------------


def test_an_authored_channel_on_a_camera_driven_property_raises():
    """The failure this replaces was SILENT.

    Camera clips are appended last and the evaluators are later-wins, so
    `set root scale_x 3.0` together with `camera.move: push_in` evaluated to
    1.25 at the shot's end — the authored value discarded, no warning. A
    deliberate divergence from `_add_face_clips`, which resolves the same
    class of collision by warning and letting the author win: a camera is not
    a face, and a silently-ignored pan is worse than a refused compile.
    """
    shot = _shot(
        Camera(move="push_in"),
        actions=[SetAction(target="root", property="scale_x", value=3.0)],
    )
    with pytest.raises(CutoutCompileError) as e:
        compile_shot(shot, fps=24, width=320, height=240)
    assert "root:scale_x" in str(e.value) and "silently" in str(e.value)


def test_a_channel_the_camera_does_not_drive_is_left_alone():
    """The rule is per-PROPERTY, not "the camera owns root". A pan drives
    pivots, so an authored scale is not a collision — and refusing it would
    make the camera exclusive over a node the author legitimately shares."""
    shot = _shot(
        Camera(move="pan_left"),
        actions=[SetAction(target="root", property="scale_x", value=3.0)],
    )
    compile_shot(shot, fps=24, width=320, height=240)  # must not raise


def test_a_channel_on_a_child_node_is_not_a_collision():
    shot = _shot(
        Camera(move="push_in"),
        actions=[SetAction(target="someone", property="scale_x", value=3.0)],
    )
    compile_shot(shot, fps=24, width=320, height=240)  # must not raise
