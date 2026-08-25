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

import json

import pytest

from an.adapters.cutout.compile import (
    CAMERA_MOVES,
    CutoutCompileError,
    PAN_FRACTION,
    camera_keys,
    compile_shot,
)
from an.ir.camera import CameraError
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


@pytest.mark.parametrize("keys", [[], [CameraKey(at=0.0, x=50.0)]])
def test_too_few_keys_is_a_warning_not_an_error(keys):
    """A pose, not a move: it renders as if the camera were absent. Worth
    saying — the author wrote a camera block that does nothing — and not worth
    failing, because nothing breaks.

    `keys=[]` is included because it produced NO finding at all while one key
    produced a warning, though both render identically (an#109 review, L-1).
    """
    report = _validate(_shot(Camera(keys=list(keys))))
    assert report.passed
    assert any("pose, not a move" in f.description for f in report.findings), [
        f.description for f in report.findings
    ]


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


# --- the review's survivors --------------------------------------------------


def test_a_pan_travels_a_third_of_the_frame():
    """`PAN_FRACTION` pinned to the NUMBER, not to itself.

    The first version of the pan-distance tests computed the expected value
    from the constant under test, so any non-zero value passed and the one
    number a named pan has to choose was guarded by nothing (an#109 review,
    H-3). This asserts the third, and a resolution where a third is an
    unmistakable 320 px.
    """
    assert PAN_FRACTION == pytest.approx(1 / 3)
    scene = compile_shot(_shot(Camera(move="pan_right")), fps=24, width=960, height=240)
    assert _channels(scene)["pivot_x"][1][1] == pytest.approx(320.0)


def test_the_camera_drives_every_key_field_it_declares():
    """`rotation` had no test, so dropping its row from `_CAMERA_CHANNELS`
    compiled a `CameraKey(rotation=…)` to NOTHING — silently — with the whole
    suite green (an#109 review, M-3/M5). Every field of `CameraKey` that names
    a property must reach one."""
    scene = compile_shot(
        _shot(Camera(keys=[
            CameraKey(at=0.0),
            CameraKey(at=2.0, x=-10.0, y=5.0, zoom=1.5, rotation=0.3),
        ])),
        fps=24,
        width=320,
        height=240,
    )
    assert set(_channels(scene)) == {"pivot_x", "pivot_y", "scale_x", "scale_y", "rotation"}
    assert _channels(scene)["rotation"][1][1] == pytest.approx(0.3)


def test_the_collision_check_sees_every_authored_animation():
    """The rule's TRIGGER was tested; its COVERAGE was not.

    Narrowing the `authored` snapshot to one animation left the detector
    half-blind and the suite stayed green (an#109 review, M-3/M6). A shot with
    several authored channels, only the LAST of which collides, is the case
    that notices.
    """
    shot = _shot(
        Camera(move="push_in"),
        actions=[
            SetAction(target="someone", property="x", value=1.0),
            SetAction(target="other", property="y", value=2.0),
            SetAction(target="root", property="scale_y", value=3.0),
        ],
    )
    with pytest.raises(CutoutCompileError, match="root:scale_y"):
        compile_shot(shot, fps=24, width=320, height=240)


def test_a_move_with_surrounding_whitespace_is_the_move():
    """`move="  "` errored at validate and no-opped at compile — the exact seam
    `camera_keys` was written to close, reintroduced on the validate side
    (an#109 review, M-1). One resolver, so there is one answer."""
    for blank in ("", "  ", "\t"):
        shot = _shot(Camera(move=blank))
        assert _validate(shot).passed, blank
        assert compile_shot(shot, fps=24, width=320, height=240).animations == {}, blank
    padded = _shot(Camera(move="  push_in  "))
    assert _validate(padded).passed
    assert set(_channels(compile_shot(padded, fps=24, width=320, height=240))) == {
        "scale_x",
        "scale_y",
    }


def test_a_single_key_does_not_become_a_two_key_animation():
    """The `len(keys) < 2` guard. Loosening it to `< 1` (or removing it) makes
    a one-key camera emit a one-keyframe channel, which the evaluator holds
    forever — a pose silently promoted to a move."""
    scene = compile_shot(
        _shot(Camera(keys=[CameraKey(at=0.0, x=50.0)])), fps=24, width=320, height=240
    )
    assert scene.animations == {} and scene.timeline.tracks == []


def test_a_zero_duration_shot_does_not_divide_by_itself():
    """`max(0.001, duration)` is the floor a named move's terminal key lands
    on. Without it a zero-duration shot emits two keyframes at the same time,
    which is a curve with no domain."""
    scene = compile_shot(
        Shot(id="s1", renderer="cutout", duration=0.0, camera=Camera(move="push_in")),
        fps=24,
        width=320,
        height=240,
    )
    times = [t for t, _, _ in _channels(scene)["scale_x"]]
    assert times[0] != times[1], times
    # …and the CLIP that plays them. `camera_keys` floors the key times and
    # the emitter floors the clip; they are two places and both matter, since
    # a zero-length clip is active at no time at all.
    (clip,) = scene.timeline.tracks[0].clips
    assert clip.duration > 0.0, clip.duration
    assert scene.animations["__camera__s1_scale_x"].duration > 0.0


# --- schema 0.3.0: the three surfaces a retired camera field arrives by ------


def test_a_stored_document_loses_the_dead_camera_fields_on_read():
    from an.ir.sync import scene_from_json_doc

    scene = scene_from_json_doc({
        "version": "0.2.0",
        "kind": "SceneIR",
        "meta": {},
        "timeline": [{
            "id": "s1",
            "renderer": "cutout",
            "duration": 1.0,
            "camera": {"position": [0.0, 0.0, 0.0], "target": [0.0, 0.0, 0.0],
                       "focal_length": 50.0, "move": "push_in"},
        }],
    })
    assert scene.timeline[0].camera.move == "push_in"
    assert not (scene.timeline[0].camera.model_extra or {})


def test_a_default_value_is_dropped_in_silence_and_a_typed_one_is_not():
    """Warning about a field the WRITER emitted would be noise on exactly the
    documents that had nothing to do with it — every `scene.md` this package
    ever generated carries all three. A value someone TYPED is different."""
    import warnings

    from an.ir.migrate import _drop_dead_camera_fields

    def migrate_one(camera):
        doc = {"version": "0.2.0", "kind": "SceneIR", "timeline": [{"id": "s1", "camera": camera}]}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            out = _drop_dead_camera_fields(doc)
        return out["timeline"][0]["camera"], [str(w.message) for w in caught]

    kept, quiet = migrate_one({"focal_length": 50.0, "position": [0.0, 0.0, 0.0], "move": "hold"})
    assert kept == {"move": "hold"} and quiet == []

    kept, loud = migrate_one({"focal_length": 85.0, "move": "hold"})
    assert kept == {"move": "hold"}
    assert len(loud) == 1 and "focal_length" in loud[0]


def test_the_markdown_surface_follows_the_same_rule():
    """`scene.md` carries no schema version, so the migration cannot reach it —
    the parser applies the rule instead."""
    import warnings

    from an.ir.sync import markdown_to_ir

    def parse(camera_yaml):
        md = (
            "# X\n\n```yaml meta\ntitle: X\nduration: 1\nfps: 24\n"
            "default_renderer: cutout\n```\n\n## Shot s1 (cutout)\n\n"
            f"```yaml shot\nduration: 1\ncamera:\n{camera_yaml}```\n"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scene = markdown_to_ir(md)
        return scene.timeline[0].camera, [str(w.message) for w in caught]

    camera, quiet = parse("  move: hold\n  focal_length: 50.0\n")
    assert not (camera.model_extra or {}) and quiet == []

    camera, loud = parse("  move: hold\n  focal_length: 85.0\n")
    assert not (camera.model_extra or {})
    assert len(loud) == 1 and "focal_length" in loud[0]


def test_a_document_already_at_the_current_version_is_caught_at_validate():
    """The one route no migration can ever reach: a camera block that came
    through a sync carrying the keys as `extra="allow"` extras keeps them
    forever, because nothing migrates a current document again."""
    shot = _shot(Camera(move="hold", focal_length=50.0))
    report = _validate(shot)
    assert report.passed  # a warning, not an error — they select nothing
    assert any("focal_length" in f.description for f in report.findings), [
        f.description for f in report.findings
    ]


def test_the_migration_does_not_mutate_the_callers_document():
    """`dict(doc)` is shallow, so `camera.pop(...)` stripped the INPUT — and
    the `return doc` shape reads as a pure function (an#109 review, M-4)."""
    from an.ir.migrate import _drop_dead_camera_fields

    doc = {"version": "0.2.0", "kind": "SceneIR",
           "timeline": [{"id": "s1", "camera": {"focal_length": 50.0, "move": "hold"}}]}
    snapshot = json.loads(json.dumps(doc))
    _drop_dead_camera_fields(doc)
    assert doc == snapshot
