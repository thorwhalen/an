"""Channel: keyframe evaluation, edge cases, easing influence."""

from __future__ import annotations

import pytest

from an.adapters.cutout.channel import Channel, Keyframe, evaluate


def test_single_keyframe_returns_constant():
    ch = Channel("a", "x", [Keyframe(0.5, 42.0)])
    assert evaluate(ch, 0.0) == 42.0
    assert evaluate(ch, 0.5) == 42.0
    assert evaluate(ch, 100.0) == 42.0


def test_clamps_before_first_and_after_last():
    ch = Channel("a", "x", [Keyframe(1.0, 10.0), Keyframe(2.0, 20.0)])
    assert evaluate(ch, 0.0) == 10.0
    assert evaluate(ch, 5.0) == 20.0


def test_linear_interpolation_at_midpoint():
    ch = Channel("a", "x", [Keyframe(0.0, 0.0), Keyframe(2.0, 10.0)])
    assert evaluate(ch, 1.0) == pytest.approx(5.0)


def test_easing_influences_intermediate_value():
    """ease_in: midpoint should fall below the linear midpoint (0.5)."""
    ch = Channel(
        "a", "x", [Keyframe(0.0, 0.0, easing="ease_in"), Keyframe(1.0, 1.0)]
    )
    v = evaluate(ch, 0.5)
    assert 0.0 < v < 0.5


def test_step_easing():
    ch = Channel(
        "a", "x", [Keyframe(0.0, 0.0, easing="step"), Keyframe(1.0, 1.0)]
    )
    assert evaluate(ch, 0.49) == 0.0
    assert evaluate(ch, 0.99) == 0.0
    assert evaluate(ch, 1.0) == 1.0


def test_three_keyframes_picks_correct_segment():
    ch = Channel(
        "a",
        "x",
        [
            Keyframe(0.0, 0.0),
            Keyframe(1.0, 10.0),
            Keyframe(2.0, 30.0),
        ],
    )
    assert evaluate(ch, 0.5) == pytest.approx(5.0)
    assert evaluate(ch, 1.5) == pytest.approx(20.0)


def test_construction_requires_at_least_one_keyframe():
    with pytest.raises(ValueError, match="at least one keyframe"):
        Channel("a", "x", [])


def test_construction_rejects_unsorted_keyframes():
    with pytest.raises(ValueError, match="sorted"):
        Channel(
            "a",
            "x",
            [Keyframe(1.0, 0.0), Keyframe(0.5, 1.0)],
        )


def test_zero_span_segment_returns_later_value():
    """Two keyframes at the same time → return the later value (no division)."""
    ch = Channel("a", "x", [Keyframe(1.0, 5.0), Keyframe(1.0, 10.0)])
    assert evaluate(ch, 1.0) == 10.0
