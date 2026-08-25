"""Timeline: track placement, overlapping clips, override semantics."""

from __future__ import annotations

import pytest

from an.adapters.cutout.channel import Channel, Keyframe
from an.adapters.cutout.clip import Clip
from an.adapters.cutout.timeline import (
    PlacedClip,
    Timeline,
    Track,
    evaluate_timeline,
    timeline_from_scene,
)


def _ramp_clip(target="a", prop="x", start=0.0, end=10.0, duration=1.0) -> Clip:
    return Clip(
        name=f"{target}_{prop}",
        duration=duration,
        channels=[
            Channel(target, prop, [Keyframe(0.0, start), Keyframe(duration, end)])
        ],
    )


def test_single_clip_evaluation():
    clip = _ramp_clip()
    tl = Timeline(duration=2.0, tracks=[Track(clips=[PlacedClip(clip, start_time=0.5)])])
    pose = evaluate_timeline(tl, 1.0)
    # local t = 1.0 - 0.5 = 0.5; clip ramps 0->10 over 1s, so pose["x"] = 5
    assert pose[("a", "x")] == pytest.approx(5.0)


def test_clip_inactive_before_start():
    clip = _ramp_clip()
    tl = Timeline(duration=5.0, tracks=[Track(clips=[PlacedClip(clip, start_time=2.0)])])
    pose = evaluate_timeline(tl, 1.0)
    assert pose == {}


def test_clip_inactive_after_end():
    clip = _ramp_clip()
    tl = Timeline(duration=5.0, tracks=[Track(clips=[PlacedClip(clip, start_time=0.0)])])
    pose = evaluate_timeline(tl, 4.0)
    # Clip ended at t=1.0; nothing active at t=4.
    assert pose == {}


def test_two_tracks_merge_distinct_targets():
    tl = Timeline(
        duration=2.0,
        tracks=[
            Track(clips=[PlacedClip(_ramp_clip(target="a", prop="x"))]),
            Track(clips=[PlacedClip(_ramp_clip(target="b", prop="y"))]),
        ],
    )
    pose = evaluate_timeline(tl, 0.5)
    assert pose[("a", "x")] == pytest.approx(5.0)
    assert pose[("b", "y")] == pytest.approx(5.0)


def test_later_track_overrides_earlier_for_same_target():
    tl = Timeline(
        duration=2.0,
        tracks=[
            Track(clips=[PlacedClip(_ramp_clip(target="a", prop="x", end=10.0))]),
            Track(clips=[PlacedClip(_ramp_clip(target="a", prop="x", end=99.0))]),
        ],
    )
    pose = evaluate_timeline(tl, 1.0)
    assert pose[("a", "x")] == pytest.approx(99.0)


def test_speed_scaling_compresses_clip():
    """speed=2.0 means the clip plays in half its natural duration."""
    clip = _ramp_clip(duration=2.0)
    tl = Timeline(
        duration=5.0,
        tracks=[Track(clips=[PlacedClip(clip, start_time=0.0, speed=2.0)])],
    )
    # effective duration = 1.0; midpoint t=0.5 → local_t=1.0 → value=5.0
    pose = evaluate_timeline(tl, 0.5)
    assert pose[("a", "x")] == pytest.approx(5.0)
    # past effective end should be inactive
    pose_after = evaluate_timeline(tl, 1.5)
    assert pose_after == {}


def test_placed_clip_validates_speed():
    clip = _ramp_clip()
    with pytest.raises(ValueError, match="speed"):
        PlacedClip(clip, speed=0)
    with pytest.raises(ValueError, match="speed"):
        PlacedClip(clip, speed=-1.0)


def test_placed_clip_validates_blend_non_negative():
    clip = _ramp_clip()
    with pytest.raises(ValueError, match="blend"):
        PlacedClip(clip, blend_in=-0.5)


def test_empty_timeline_returns_empty_pose():
    tl = Timeline(duration=1.0, tracks=[])
    assert evaluate_timeline(tl, 0.5) == {}


def test_end_of_timeline_holds_final_frame():
    """At exactly t == timeline.duration, the final clip's end value should hold."""
    clip = _ramp_clip(start=0.0, end=10.0, duration=1.0)
    tl = Timeline(
        duration=1.0, tracks=[Track(clips=[PlacedClip(clip, start_time=0.0)])]
    )
    pose = evaluate_timeline(tl, 1.0)
    assert pose[("a", "x")] == pytest.approx(10.0)


# --- an#107: the compiled document, evaluated without a browser ---------------


def _compiled(actions, *, duration=2.0):
    """A real `compile_shot` document — not a hand-built one.

    The point of `timeline_from_scene` is the *compiler's* output, so a
    hand-assembled `CutoutSceneJSON` would test the reader against a fixture
    nobody produces.
    """
    from an.adapters.cutout.compile import compile_shot
    from an.ir.schema import Shot

    shot = Shot(id="s1", renderer="cutout", duration=duration, actions=list(actions))
    return compile_shot(shot, mall=None, fps=24)


def test_a_compiled_scene_evaluates_at_a_time_without_a_browser():
    """The helper five test modules shared, promoted out of a test file (an#107).

    Measuring a pan needs the *composed* screen-space position of a node, and
    composition starts from a pose. Nothing in `an/` could produce a pose from
    a compiled document except a private function in
    `tests/test_swap_channels.py`, imported by five sibling test modules —
    which is not a place a package can build a measurement on.
    """
    from an.ir.compose import tween

    scene = _compiled([tween("root", "x", 10.0, 1.0, from_=0.0)])
    tl = timeline_from_scene(scene)
    pose = evaluate_timeline(tl, 0.5)
    assert pose[("root", "x")] == pytest.approx(5.0)

    # `target_root` is the subtree a track drives. `evaluate_timeline` never
    # reads it — poses are keyed by channel target — so nothing else here would
    # notice it being dropped. The compositor an#111 needs is exactly the
    # consumer that will, so carry it faithfully now.
    # `target_root` is the subtree a track drives, and track ORDER is the
    # documented priority rule ("last track wins on conflict") — which
    # `evaluate_timeline` does read. Both pinned as sequences, so a reordering
    # or a dropped root fails here rather than in a blink test three modules
    # away that is about something else.
    assert [t.target_root for t in tl.tracks] == [
        t.target_root for t in scene.timeline.tracks
    ]
    assert tl.duration == scene.timeline.duration

    assert [p.duration for p in tl.tracks[0].clips] == [
        p.duration for p in scene.timeline.tracks[0].clips
    ]


def test_the_reader_carries_loop_mode():
    """A looping clip evaluated as `once` is the an#7 bug, and it is silent:
    every assertion routed through the helper still passes, just against the
    wrong pose. `LoopMode` is carried, never defaulted."""
    from an.adapters.cutout.clip import LoopMode

    scene = _compiled([])
    # Build it explicitly rather than through the compiler: no shipped emitter
    # produces `ping_pong`, and `loop` needs a descriptor animation.
    from an.adapters.cutout.serialize import (
        AnimationClipJSON,
        ChannelJSON,
        KeyframeJSON,
        PlacedClipJSON,
        TimelineJSON,
        TrackJSON,
    )

    scene.animations = {
        "loopy": AnimationClipJSON(
            name="loopy",
            duration=1.0,
            loop_mode="loop",
            channels=[
                ChannelJSON(
                    target="a",
                    property="x",
                    keyframes=[KeyframeJSON(time=0.0, value=0.0), KeyframeJSON(time=1.0, value=10.0)],
                )
            ],
        )
    }
    scene.timeline = TimelineJSON(
        duration=3.0,
        tracks=[TrackJSON(target_root="a", clips=[PlacedClipJSON(animation_id="loopy", duration=3.0)])],
    )
    tl = timeline_from_scene(scene)
    assert tl.tracks[0].clips[0].clip.loop_mode is LoopMode.LOOP
    # …and it evaluates as a loop: t=1.5 is half way through the second pass.
    assert evaluate_timeline(tl, 1.5)[("a", "x")] == pytest.approx(5.0)


def test_the_reader_carries_a_cubic_bezier_easing_as_a_tuple():
    """A list-valued easing is a 4-control-point cubic bezier. `Keyframe`
    compares and hashes it; a list would be neither."""
    from an.ir.compose import tween

    scene = _compiled([tween("root", "x", 10.0, 1.0, from_=0.0, easing=[0.4, 0.0, 0.2, 1.0])])
    tl = timeline_from_scene(scene)
    easings = [
        k.easing
        for clip in tl.tracks[0].clips
        for ch in clip.clip.channels
        for k in ch.keyframes
    ]
    assert any(isinstance(e, tuple) and len(e) == 4 for e in easings), easings
    assert not any(isinstance(e, list) for e in easings), easings


def test_a_dangling_animation_id_is_loud():
    """A track naming an animation the document does not carry is a compiler
    bug, and the reader's job is to say so where it happens. Degrading to an
    empty clip would render a still frame that looks like a deliberate hold."""
    from an.adapters.cutout.serialize import PlacedClipJSON, TimelineJSON, TrackJSON

    scene = _compiled([])
    scene.animations = {}
    scene.timeline = TimelineJSON(
        duration=1.0,
        tracks=[TrackJSON(target_root="a", clips=[PlacedClipJSON(animation_id="ghost")])],
    )
    with pytest.raises(KeyError, match="ghost"):
        timeline_from_scene(scene)


def test_every_placement_field_survives_the_read():
    """Field fidelity needs a document whose fields DIFFER.

    The compiler's own output is uniform — one track per entity, every
    placement at `start_time=0.0` with `blend_in/out = 0.0` — so asserting
    fidelity against it is vacuous, and measurably so: with the compiled
    fixture, dropping `start_time`, dropping both blend ramps and reversing
    the track order all survived (an#107 review, M1/M2/M4). This one is
    hand-built on purpose, with three tracks and no two fields alike.

    `start_time` and `speed` were previously pinned only by blink and viseme
    tests in five other modules — guards that evaporate the day one of those
    is rewritten, because they are about animation semantics, not about this
    reader. `blend_in`/`blend_out` were pinned by nothing at all: they are
    declared by `PlacedClipJSON`, accepted by `PlacedClip`, and read by no
    one yet — `evaluate_timeline` records the ramps and does not apply them
    (additive blending is 2B) — so the reader silently dropped them and the
    whole suite stayed green. That is the same argument that carries
    `target_root`, and it got the opposite answer in the same function.
    """
    from an.adapters.cutout.serialize import (
        AnimationClipJSON,
        ChannelJSON,
        KeyframeJSON,
        PlacedClipJSON,
        TimelineJSON,
        TrackJSON,
    )

    scene = _compiled([])
    scene.animations = {
        name: AnimationClipJSON(
            name=name,
            duration=1.0,
            channels=[
                ChannelJSON(
                    target=name,
                    property="x",
                    keyframes=[KeyframeJSON(time=0.0, value=0.0), KeyframeJSON(time=1.0, value=1.0)],
                )
            ],
        )
        for name in ("a", "b", "c")
    }
    scene.timeline = TimelineJSON(
        duration=9.0,
        tracks=[
            TrackJSON(
                target_root=name,
                clips=[
                    PlacedClipJSON(
                        animation_id=name,
                        start_time=start,
                        duration=dur,
                        speed=speed,
                        blend_in=bi,
                        blend_out=bo,
                    )
                ],
            )
            for name, start, dur, speed, bi, bo in (
                ("a", 0.5, 2.0, 1.5, 0.1, 0.2),
                ("b", 3.25, None, 0.5, 0.3, 0.0),
                ("c", 6.0, 1.0, 2.0, 0.0, 0.4),
            )
        ],
    )
    tl = timeline_from_scene(scene)

    # Track ORDER is the documented priority rule ("last track wins on
    # conflict") and `evaluate_timeline` does read it — so it is asserted as a
    # sequence, not a set.
    assert [t.target_root for t in tl.tracks] == ["a", "b", "c"]
    placed = [p for t in tl.tracks for p in t.clips]
    assert [p.start_time for p in placed] == [0.5, 3.25, 6.0]
    assert [p.duration for p in placed] == [2.0, None, 1.0]
    assert [p.speed for p in placed] == [1.5, 0.5, 2.0]
    assert [(p.blend_in, p.blend_out) for p in placed] == [(0.1, 0.2), (0.3, 0.0), (0.0, 0.4)]
    assert [p.clip.name for p in placed] == ["a", "b", "c"]

    # Which of the two names is authoritative, when they disagree? The MAP KEY:
    # `PlacedClipJSON.animation_id` is documented as "name lookup into the
    # AnimationClipJSON map", so the key is what a track can actually reach and
    # `AnimationClipJSON.name` is a label. The compiler always writes them
    # equal, which is exactly why a document where they differ is the only way
    # to pin the answer.
    scene.animations = {"under_key": scene.animations["a"].model_copy(update={"name": "label"})}
    scene.timeline = TimelineJSON(
        duration=1.0,
        tracks=[TrackJSON(target_root="a", clips=[PlacedClipJSON(animation_id="under_key")])],
    )
    assert timeline_from_scene(scene).tracks[0].clips[0].clip.name == "under_key"
