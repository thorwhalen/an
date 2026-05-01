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
