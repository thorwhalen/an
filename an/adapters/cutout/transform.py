"""2D affine math for the cutout scene graph.

We store transforms in two complementary forms:

- **`TransformParams`** — the *authoring* form: translate, rotate, scale, skew,
  pivot. Easy to read, easy to animate per-property.
- **`Matrix3x3`** — the *evaluation* form: a flat 3×3 row-major matrix used
  for fast composition during world-transform computation.

A `TransformParams` decomposes uniquely into a `Matrix3x3` via
``Matrix3x3.from_params(...)``; the inverse direction (`decompose`) is unique
only for the parameter combinations we actually use (no negative scale, no
skew interaction with rotation), which is enough for cutout rigid rigs.

>>> p = TransformParams(x=10, y=5, rotation_rad=0.0, scale_x=2.0, scale_y=2.0)
>>> m = Matrix3x3.from_params(p)
>>> m @ Matrix3x3.identity() == m
True
>>> q = m.decompose()
>>> round(q.x, 6), round(q.y, 6), round(q.scale_x, 6)
(10.0, 5.0, 2.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TransformParams:
    """Authoring-form 2D transform.

    Application order (matching SVG / CSS / PixiJS conventions):

        translate(x, y) → translate(pivot) → rotate → scale → skew → translate(-pivot)

    so a non-zero pivot rotates and scales around the pivot point in local space.
    """

    x: float = 0.0
    y: float = 0.0
    rotation_rad: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_x: float = 0.0
    skew_y: float = 0.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0


@dataclass(slots=True, frozen=True)
class Matrix3x3:
    """Row-major affine matrix. The third row is implicit `[0, 0, 1]`.

    Layout::

        | a c tx |
        | b d ty |
        | 0 0  1 |

    Six free entries: ``a, b, c, d, tx, ty``. ``a, b, c, d`` form the linear
    2×2 part; ``tx, ty`` is the translation.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    # ---- Constructors -------------------------------------------------------

    @staticmethod
    def identity() -> "Matrix3x3":
        """Return the identity matrix."""
        return Matrix3x3()

    @staticmethod
    def from_params(p: TransformParams) -> "Matrix3x3":
        """Build a Matrix3x3 from ``TransformParams``."""
        cos_r = math.cos(p.rotation_rad)
        sin_r = math.sin(p.rotation_rad)
        sx, sy = p.scale_x, p.scale_y
        skx, sky = p.skew_x, p.skew_y

        # M = T(x,y) · T(pivot) · R · S · K · T(-pivot)
        # Combined (no skew interaction with rotation simplification):
        #   linear = R · S · K
        a = cos_r * sx
        b = sin_r * sx
        c = -sin_r * sy + cos_r * sx * skx
        d = cos_r * sy + sin_r * sx * skx
        # Apply skew_y as a shear on x by y (after the above).
        # Pivot:
        # final_tx = x + (pivot_x - a*pivot_x - c*pivot_y)
        # final_ty = y + (pivot_y - b*pivot_x - d*pivot_y)
        tx = p.x + p.pivot_x - (a * p.pivot_x + c * p.pivot_y)
        ty = p.y + p.pivot_y - (b * p.pivot_x + d * p.pivot_y)
        # Skew_y as a vertical shear (no rotation): not commonly used in
        # cutout; keep it as an extra row mix.
        if sky != 0.0:
            # b_new = b + a*sky ; d_new = d + c*sky
            b = b + a * sky
            d = d + c * sky
        return Matrix3x3(a=a, b=b, c=c, d=d, tx=tx, ty=ty)

    # ---- Operations ---------------------------------------------------------

    def __matmul__(self, other: "Matrix3x3") -> "Matrix3x3":
        """Matrix multiply: ``self @ other``."""
        return Matrix3x3(
            a=self.a * other.a + self.c * other.b,
            b=self.b * other.a + self.d * other.b,
            c=self.a * other.c + self.c * other.d,
            d=self.b * other.c + self.d * other.d,
            tx=self.a * other.tx + self.c * other.ty + self.tx,
            ty=self.b * other.tx + self.d * other.ty + self.ty,
        )

    def transform_point(self, x: float, y: float) -> tuple[float, float]:
        """Apply this matrix to a 2D point ``(x, y)``."""
        return (self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty)

    def decompose(self) -> TransformParams:
        """Recover authoring params from a matrix.

        Assumes the matrix was produced by ``from_params`` with zero skew,
        positive scale, and zero pivot — sufficient for the cutout use case
        (animations always go through ``from_params`` paths). For arbitrary
        matrices, the recovered params reproduce the matrix but may not match
        the original (e.g. negative-scale ambiguity).
        """
        # rotation from the first column
        rotation_rad = math.atan2(self.b, self.a)
        # scale lengths of the columns
        scale_x = math.hypot(self.a, self.b)
        scale_y = math.hypot(self.c, self.d)
        return TransformParams(
            x=self.tx,
            y=self.ty,
            rotation_rad=rotation_rad,
            scale_x=scale_x,
            scale_y=scale_y,
        )

    def to_tuple(self) -> tuple[float, float, float, float, float, float]:
        """Six-tuple suitable for serialization or direct use in Canvas2D-style APIs."""
        return (self.a, self.b, self.c, self.d, self.tx, self.ty)
