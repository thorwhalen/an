"""Easing functions for keyframe interpolation.

Two forms are supported (matching `an.base.EasingSpec`):

- A **named preset** string from ``an.base.EASING_PRESETS``.
- A **cubic-Bézier control 4-tuple** `[cx1, cy1, cx2, cy2]` over the unit square.

``apply_easing(spec, t)`` is the dispatcher; named presets are resolved via
``EASING_FUNCS``. Step easing returns 0 until t==1.

>>> apply_easing("linear", 0.5)
0.5
>>> round(apply_easing("ease_in_out", 0.5), 6)
0.5
>>> round(apply_easing([0.0, 0.0, 1.0, 1.0], 0.5), 6)
0.5
"""

from __future__ import annotations

from typing import Callable, Sequence

from an.base import EasingSpec


# -----------------------------------------------------------------------------
# Named presets
# -----------------------------------------------------------------------------


def _linear(t: float) -> float:
    return t


def _ease_in(t: float) -> float:
    return t * t


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 2


def _ease_in_out(t: float) -> float:
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - 2.0 * (1.0 - t) ** 2


def _ease(t: float) -> float:
    """Default smoothstep — matches CSS `ease` (slight ease in, longer ease out)."""
    return _ease_in_out(t)


def _step(t: float) -> float:
    return 0.0 if t < 1.0 else 1.0


EASING_FUNCS: dict[str, Callable[[float], float]] = {
    "linear": _linear,
    "ease": _ease,
    "ease_in": _ease_in,
    "ease_out": _ease_out,
    "ease_in_out": _ease_in_out,
    "step": _step,
}


# -----------------------------------------------------------------------------
# Cubic-Bézier
# -----------------------------------------------------------------------------


def cubic_bezier(cx1: float, cy1: float, cx2: float, cy2: float, t: float) -> float:
    """Evaluate a 1D cubic-Bézier easing curve at parameter ``t`` ∈ [0, 1].

    The curve is defined by P0=(0,0), P1=(cx1,cy1), P2=(cx2,cy2), P3=(1,1).
    Given a desired x=t we solve for the matching curve parameter u, then
    return the y coordinate. Newton's-method approximation; 8 iterations is
    visually indistinguishable from analytic.

    >>> round(cubic_bezier(0.0, 0.0, 1.0, 1.0, 0.5), 6)  # linear
    0.5
    >>> round(cubic_bezier(0.42, 0.0, 0.58, 1.0, 0.0), 6)  # endpoints exact
    0.0
    >>> round(cubic_bezier(0.42, 0.0, 0.58, 1.0, 1.0), 6)
    1.0
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0

    def bx(u: float) -> float:
        # x coordinate of the curve at parameter u (note P0.x=0, P3.x=1)
        return 3 * (1 - u) ** 2 * u * cx1 + 3 * (1 - u) * u * u * cx2 + u**3

    def dbx(u: float) -> float:
        return (
            3 * (1 - u) ** 2 * cx1
            - 6 * (1 - u) * u * cx1
            + 6 * (1 - u) * u * cx2
            - 3 * u * u * cx2
            + 3 * u * u
        )

    def by(u: float) -> float:
        return 3 * (1 - u) ** 2 * u * cy1 + 3 * (1 - u) * u * u * cy2 + u**3

    # Newton's method to find u such that bx(u) = t
    u = t
    for _ in range(8):
        f = bx(u) - t
        fp = dbx(u)
        if abs(fp) < 1e-12:
            break
        u_new = u - f / fp
        # Clamp into the valid range so we don't escape during iteration.
        if u_new < 0.0:
            u_new = 0.0
        elif u_new > 1.0:
            u_new = 1.0
        if abs(u_new - u) < 1e-9:
            u = u_new
            break
        u = u_new
    return by(u)


# -----------------------------------------------------------------------------
# Dispatcher
# -----------------------------------------------------------------------------


def apply_easing(spec: EasingSpec | None, t: float) -> float:
    """Apply an easing spec to a normalized parameter ``t`` ∈ [0, 1].

    Accepts:

    - ``None`` → linear (passthrough)
    - a string preset name (must be a key of ``EASING_FUNCS``)
    - a 4-element sequence of cubic-Bézier control points

    Raises ``ValueError`` for unknown preset names or malformed sequences.

    >>> apply_easing(None, 0.25)
    0.25
    >>> apply_easing("step", 0.99)
    0.0
    >>> apply_easing("step", 1.0)
    1.0
    """
    if spec is None:
        return t
    if isinstance(spec, str):
        try:
            return EASING_FUNCS[spec](t)
        except KeyError as e:
            raise ValueError(
                f"unknown easing preset {spec!r}; known: {sorted(EASING_FUNCS)}"
            ) from e
    if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
        if len(spec) != 4:
            raise ValueError(
                f"cubic-bezier easing requires exactly 4 control values, got {len(spec)}"
            )
        cx1, cy1, cx2, cy2 = (float(v) for v in spec)
        return cubic_bezier(cx1, cy1, cx2, cy2, t)
    raise TypeError(f"unsupported easing spec type: {type(spec).__name__}")
