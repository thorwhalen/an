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


def test_non_numeric_values_ignore_easing_and_hold_until_the_next_keyframe():
    """Step semantics is a theorem, not an easing convention (an#86).

    The old rule snapped on the EASED position, which an overshooting cubic
    bezier crosses mid-segment — (0.5,2,0.5,2) showed the second key from
    u≈0.257, and (0.3,3,0.7,0) flapped A→B→A within one segment. The snap is
    now on the raw segment position, which the keyframe scan keeps strictly
    below 1 inside a segment, so easing cannot move a swap.
    """
    for easing in (
        "linear",
        "ease",
        "ease_in_out",
        "step",
        (0.5, 2.0, 0.5, 2.0),  # overshoot: early-snap under the old rule
        (0.3, 3.0, 0.7, 0.0),  # overshoot: A→B→A flapping under the old rule
    ):
        ch = Channel("a", "hands", [Keyframe(0.0, "A", easing), Keyframe(1.0, "B")])
        for t in (0.0, 0.146, 0.257, 0.3, 0.5, 0.66, 0.9999999):
            assert evaluate(ch, t) == "A", (easing, t)
        assert evaluate(ch, 1.0) == "B"


def test_non_numeric_values_still_validate_the_easing():
    """Easing is never APPLIED to a swap value, but a typo'd name stays loud."""
    ch = Channel("a", "hands", [Keyframe(0.0, "A", "eaze"), Keyframe(1.0, "B")])
    with pytest.raises(ValueError, match="unknown easing preset"):
        evaluate(ch, 0.5)


def test_bool_values_snap_rather_than_lerp():
    """Python's ``bool ⊂ int`` must not lerp what JS's ``typeof`` snaps."""
    ch = Channel("a", "visible", [Keyframe(0.0, True, "linear"), Keyframe(1.0, False)])
    assert evaluate(ch, 0.25) is True
    assert evaluate(ch, 1.0) is False
