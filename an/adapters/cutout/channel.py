"""Channel: keyframes for a single (target, property) pair, evaluated at time t.

A channel holds a sorted list of `Keyframe`s. ``evaluate(channel, t)`` does a
binary search to find the surrounding keyframes, applies the easing for that
segment, and lerps between the two values.

Phase 2A supports **numeric values only** (int / float). Vector values
(positions as `(x, y)` pairs), color tweens, and string-attachment swaps
arrive in 2B.

>>> ch = Channel("a", "x", [Keyframe(0.0, 0.0), Keyframe(1.0, 10.0)])
>>> evaluate(ch, 0.5)
5.0
>>> evaluate(ch, -1.0)  # before first → clamps to first value
0.0
>>> evaluate(ch, 99.0)  # after last → clamps to last value
10.0
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any

from an.adapters.cutout.easing import apply_easing
from an.base import EasingSpec


@dataclass(slots=True, frozen=True)
class Keyframe:
    """One keyframe: time, value, optional per-segment easing.

    The easing on a keyframe describes the curve **leaving** that keyframe
    toward the next one. The last keyframe's easing is therefore unused.
    """

    time: float
    value: Any
    easing: EasingSpec | None = None


@dataclass(slots=True)
class Channel:
    """Sorted keyframes for one property of one target.

    Construction validates that ``keyframes`` is non-empty and sorted.
    """

    target: str
    property: str
    keyframes: list[Keyframe] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.keyframes:
            raise ValueError(
                f"Channel({self.target!r}, {self.property!r}) requires at least one keyframe"
            )
        # Validate sorted order; cheap and avoids subtle eval bugs.
        prev_t = -float("inf")
        for kf in self.keyframes:
            if kf.time < prev_t:
                raise ValueError(
                    f"Channel({self.target!r}, {self.property!r}) keyframes must be "
                    f"sorted by time; got {kf.time} after {prev_t}"
                )
            prev_t = kf.time


def evaluate(channel: Channel, t: float) -> Any:
    """Evaluate ``channel`` at time ``t``."""
    kfs = channel.keyframes
    if len(kfs) == 1:
        return kfs[0].value
    times = [kf.time for kf in kfs]
    if t >= times[-1]:
        return kfs[-1].value
    if t < times[0]:
        return kfs[0].value
    # Binary search: find rightmost index i such that times[i] <= t.
    i = bisect.bisect_right(times, t) - 1
    a = kfs[i]
    b = kfs[i + 1]
    span = b.time - a.time
    if span <= 0.0:
        return b.value
    u = (t - a.time) / span
    eased = apply_easing(a.easing, u)
    return _lerp(a.value, b.value, eased)


def _lerp(a: Any, b: Any, t: float) -> Any:
    """Linear interpolation between ``a`` and ``b`` at parameter ``t``.

    Numeric only in Phase 2A. Step interpolation is achieved via
    ``easing="step"``, which makes ``t`` either 0 or 1.
    """
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * t
    # Fallback: treat as discrete (return ``b`` once we've crossed the midpoint).
    return b if t >= 0.5 else a
