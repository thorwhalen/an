"""Clip evaluation, loop modes, multi-channel pose collection."""

from __future__ import annotations

import pytest

from an.adapters.cutout.channel import Channel, Keyframe
from an.adapters.cutout.clip import Clip, LoopMode, evaluate


def _two_channel_clip(duration=1.0, loop_mode=LoopMode.ONCE) -> Clip:
    return Clip(
        name="test",
        duration=duration,
        loop_mode=loop_mode,
        channels=[
            Channel("a", "x", [Keyframe(0.0, 0.0), Keyframe(duration, 10.0)]),
            Channel("a", "y", [Keyframe(0.0, 100.0), Keyframe(duration, 200.0)]),
        ],
    )


def test_clip_evaluate_collects_multi_channel_pose():
    clip = _two_channel_clip()
    pose = evaluate(clip, 0.5)
    assert pose == {("a", "x"): pytest.approx(5.0), ("a", "y"): pytest.approx(150.0)}


def test_clip_once_holds_past_duration():
    clip = _two_channel_clip(duration=1.0, loop_mode=LoopMode.ONCE)
    pose = evaluate(clip, 5.0)
    assert pose[("a", "x")] == pytest.approx(10.0)
    assert pose[("a", "y")] == pytest.approx(200.0)


def test_clip_loop_wraps_modulo_duration():
    clip = _two_channel_clip(duration=2.0, loop_mode=LoopMode.LOOP)
    # t=2.5 should evaluate at wrapped=0.5
    pose_at_25 = evaluate(clip, 2.5)
    pose_at_05 = evaluate(clip, 0.5)
    assert pose_at_25 == pose_at_05


def test_clip_ping_pong_returns_to_start():
    """Over a 2-second clip, t=2 is the apex; t=4 is back at t=0."""
    clip = _two_channel_clip(duration=2.0, loop_mode=LoopMode.PING_PONG)
    pose_at_0 = evaluate(clip, 0.0)
    pose_at_4 = evaluate(clip, 4.0)
    assert pose_at_0[("a", "x")] == pytest.approx(pose_at_4[("a", "x")])


def test_clip_ping_pong_apex_at_duration():
    clip = _two_channel_clip(duration=2.0, loop_mode=LoopMode.PING_PONG)
    pose_at_d = evaluate(clip, 2.0)
    pose_at_3 = evaluate(clip, 3.0)
    # At t=3 the bounce has gone half-way back, so x should equal x at t=1.
    pose_at_1 = evaluate(clip, 1.0)
    assert pose_at_3[("a", "x")] == pytest.approx(pose_at_1[("a", "x")])
    # At apex, max value reached.
    assert pose_at_d[("a", "x")] == pytest.approx(10.0)


def test_clip_negative_t_clamps():
    clip = _two_channel_clip()
    pose = evaluate(clip, -1.0)
    assert pose[("a", "x")] == 0.0


def test_clip_zero_duration_rejected():
    with pytest.raises(ValueError, match="duration must be"):
        Clip(name="bad", duration=0)
    with pytest.raises(ValueError, match="duration must be"):
        Clip(name="bad", duration=-1.0)


def test_loop_mode_string_serializable():
    """LoopMode is a str-Enum so it survives JSON serialization."""
    assert LoopMode.LOOP.value == "loop"
    assert LoopMode.PING_PONG.value == "ping_pong"
