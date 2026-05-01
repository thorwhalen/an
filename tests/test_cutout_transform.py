"""Cutout transform: matrix algebra and decompose/compose round-trip."""

from __future__ import annotations

import math

import pytest

from an.adapters.cutout.transform import Matrix3x3, TransformParams


def _approx_eq(m1: Matrix3x3, m2: Matrix3x3, tol: float = 1e-9) -> bool:
    return all(
        abs(getattr(m1, k) - getattr(m2, k)) < tol
        for k in ("a", "b", "c", "d", "tx", "ty")
    )


def test_identity_is_identity():
    i = Matrix3x3.identity()
    assert (i.a, i.b, i.c, i.d, i.tx, i.ty) == (1, 0, 0, 1, 0, 0)


def test_identity_is_left_and_right_neutral():
    i = Matrix3x3.identity()
    m = Matrix3x3(a=2, b=1, c=3, d=4, tx=5, ty=6)
    assert _approx_eq(m @ i, m)
    assert _approx_eq(i @ m, m)


def test_associativity():
    a = Matrix3x3.from_params(TransformParams(x=1, y=2, rotation_rad=0.3, scale_x=2, scale_y=2))
    b = Matrix3x3.from_params(TransformParams(x=3, y=-1, rotation_rad=-0.2))
    c = Matrix3x3.from_params(TransformParams(scale_x=0.5, scale_y=0.5))
    assert _approx_eq((a @ b) @ c, a @ (b @ c), tol=1e-9)


def test_translate_only_decompose_roundtrip():
    p = TransformParams(x=10, y=-5)
    m = Matrix3x3.from_params(p)
    q = m.decompose()
    assert q.x == pytest.approx(10)
    assert q.y == pytest.approx(-5)
    assert q.rotation_rad == pytest.approx(0)
    assert q.scale_x == pytest.approx(1)
    assert q.scale_y == pytest.approx(1)


def test_rotate_scale_decompose_roundtrip():
    p = TransformParams(x=3, y=4, rotation_rad=math.pi / 4, scale_x=2.0, scale_y=2.0)
    m = Matrix3x3.from_params(p)
    q = m.decompose()
    assert q.x == pytest.approx(3)
    assert q.y == pytest.approx(4)
    assert q.rotation_rad == pytest.approx(math.pi / 4)
    assert q.scale_x == pytest.approx(2.0)
    assert q.scale_y == pytest.approx(2.0)


def test_transform_point_identity():
    i = Matrix3x3.identity()
    assert i.transform_point(3, 4) == (3, 4)


def test_transform_point_translation():
    m = Matrix3x3.from_params(TransformParams(x=10, y=20))
    assert m.transform_point(0, 0) == (10, 20)
    assert m.transform_point(1, 2) == (11, 22)


def test_transform_point_rotation_90deg():
    m = Matrix3x3.from_params(TransformParams(rotation_rad=math.pi / 2))
    x, y = m.transform_point(1, 0)
    assert x == pytest.approx(0, abs=1e-9)
    assert y == pytest.approx(1, abs=1e-9)


def test_transform_point_scale():
    m = Matrix3x3.from_params(TransformParams(scale_x=2.0, scale_y=3.0))
    assert m.transform_point(1, 1) == pytest.approx((2, 3))


def test_pivot_rotates_around_pivot_point():
    # Rotate 180° around (10, 0); the point (10, 0) should be a fixed point.
    m = Matrix3x3.from_params(
        TransformParams(rotation_rad=math.pi, pivot_x=10, pivot_y=0)
    )
    x, y = m.transform_point(10, 0)
    assert x == pytest.approx(10, abs=1e-9)
    assert y == pytest.approx(0, abs=1e-9)


def test_to_tuple_six_values():
    m = Matrix3x3(a=2, b=1, c=3, d=4, tx=5, ty=6)
    assert m.to_tuple() == (2, 1, 3, 4, 5, 6)


def test_compose_translate_then_rotate():
    """Translate then rotate around origin: the translation gets rotated."""
    t = Matrix3x3.from_params(TransformParams(x=10, y=0))
    r = Matrix3x3.from_params(TransformParams(rotation_rad=math.pi / 2))
    # Apply rotation first, then translation: r @ t means t is the inner.
    composed = r @ t
    # Origin point becomes (0, 10) after rotation of (10, 0).
    x, y = composed.transform_point(0, 0)
    assert x == pytest.approx(0, abs=1e-9)
    assert y == pytest.approx(10, abs=1e-9)
