"""Easing dispatcher + cubic-Bézier evaluator."""

from __future__ import annotations

import pytest

from an.adapters.cutout.easing import (
    EASING_FUNCS,
    apply_easing,
    cubic_bezier,
)


def test_all_known_presets_endpoints():
    for name in EASING_FUNCS:
        # Step is the only preset with a discontinuous start; everything else
        # should pass through (0,0) and (1,1).
        if name == "step":
            assert EASING_FUNCS[name](1.0) == 1.0
            assert EASING_FUNCS[name](0.999) == 0.0
            continue
        assert EASING_FUNCS[name](0.0) == pytest.approx(0.0, abs=1e-6)
        assert EASING_FUNCS[name](1.0) == pytest.approx(1.0, abs=1e-6)


def test_linear_passthrough():
    assert apply_easing(None, 0.3) == 0.3
    assert apply_easing("linear", 0.7) == 0.7


def test_ease_in_concave_up():
    """ease_in starts slow: f(0.25) should be < 0.25."""
    assert apply_easing("ease_in", 0.25) < 0.25
    assert apply_easing("ease_in", 0.75) > 0.5  # but past midpoint it accelerates


def test_ease_out_starts_fast():
    """ease_out: f(0.25) should already be past 0.25."""
    assert apply_easing("ease_out", 0.25) > 0.25


def test_ease_in_out_symmetric_around_half():
    """ease_in_out is symmetric: f(0.5) == 0.5."""
    assert apply_easing("ease_in_out", 0.5) == pytest.approx(0.5, abs=1e-6)


def test_unknown_preset_raises():
    with pytest.raises(ValueError, match="unknown easing"):
        apply_easing("bogus", 0.5)


def test_cubic_bezier_linear_equivalence():
    """Bezier with P1=(0,0) P2=(1,1) is linear."""
    for t in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert cubic_bezier(0, 0, 1, 1, t) == pytest.approx(t, abs=1e-6)


def test_cubic_bezier_endpoints():
    assert cubic_bezier(0.42, 0.0, 0.58, 1.0, 0.0) == 0.0
    assert cubic_bezier(0.42, 0.0, 0.58, 1.0, 1.0) == 1.0


def test_cubic_bezier_monotonic_for_monotonic_curve():
    """A standard ease curve should be monotonically non-decreasing."""
    prev = 0.0
    for i in range(11):
        t = i / 10.0
        v = cubic_bezier(0.25, 0.1, 0.25, 1.0, t)
        assert v + 1e-6 >= prev
        prev = v


def test_apply_easing_with_4_tuple():
    v = apply_easing([0.0, 0.0, 1.0, 1.0], 0.5)
    assert v == pytest.approx(0.5, abs=1e-6)


def test_apply_easing_with_4_list_via_dispatch():
    v = apply_easing((0.42, 0.0, 0.58, 1.0), 0.5)
    assert 0.4 < v < 0.6


def test_apply_easing_wrong_length_raises():
    with pytest.raises(ValueError, match="exactly 4"):
        apply_easing([0.5, 0.5, 0.5], 0.5)


def test_apply_easing_unsupported_type_raises():
    with pytest.raises(TypeError):
        apply_easing(42, 0.5)
