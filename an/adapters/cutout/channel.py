"""Channel: keyframes for a single (target, property) pair, evaluated at time t.

A channel holds a sorted list of `Keyframe`s. ``evaluate(channel, t)`` does a
binary search to find the surrounding keyframes, applies the easing for that
segment, and lerps between the two values.

**This module is the executable spec of ``runtime.js``'s ``evaluateChannel``**
— the browser implementation must stay behaviourally identical, and
``tests/test_cutout_channel_parity.py`` runs the real extracted JS against this
one to pin it (the same harness pattern that pins ``wrapTime``).

Two value classes, two rules:

- **Numeric** (``int``/``float``, excluding ``bool``): true interpolation
  through the segment's easing.
- **Everything else** (strings — viseme codes, swap keys): the value holds
  ``a`` for exactly ``[a.time, b.time)`` and switches at ``b.time``.
  **Easing does not apply** — the snap compares ``t`` against ``b.time``
  directly, never an eased or derived parameter, because each indirection was
  measured wrong: an overshooting cubic-bezier easing crosses 1.0 mid-segment
  (showing the *second* key early, or flapping A→B→A within one segment), and
  even the raw ``(t - a.time) / span`` can round up to 1.0 while
  ``t < b.time``. The time comparison has no intermediate arithmetic, so step
  semantics is a theorem here, not a convention. The easing is still
  *validated* (an unknown spec raises) so a typo'd easing name stays loud on
  every channel.

``bool`` keyframe values are refused upstream by the compiler
(``compile.py::_check_keyframe_value``): Python's ``isinstance(True, int)``
would lerp what JS's ``typeof`` snaps.

>>> ch = Channel("a", "x", [Keyframe(0.0, 0.0), Keyframe(1.0, 10.0)])
>>> evaluate(ch, 0.5)
5.0
>>> evaluate(ch, -1.0)  # before first → clamps to first value
0.0
>>> evaluate(ch, 99.0)  # after last → clamps to last value
10.0
>>> sw = Channel("a", "hands", [Keyframe(0.0, "fist"), Keyframe(1.0, "open")])
>>> evaluate(sw, 0.999)  # holds the first key for the whole segment
'fist'
>>> evaluate(sw, 1.0)  # switches exactly at the keyframe
'open'
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
    # Validated for every segment (a typo'd easing name must raise on a swap
    # channel too), but *applied* only to numeric values — see module docstring.
    eased = apply_easing(a.easing, u)
    if _is_numeric(a.value) and _is_numeric(b.value):
        return a.value + (b.value - a.value) * eased
    # Non-numeric: snap on TIME, never on the (eased or raw) parameter. The
    # value is `a` for exactly [a.time, b.time) — comparing `u >= 1.0` here
    # would be one float-division away from wrong: (t - a.time) / span can
    # round UP to 1.0 while t < b.time, showing `b` one representable float
    # early (found by the an#86 adversarial review). The time comparison has
    # no intermediate arithmetic, so the boundary is exact.
    return b.value if t >= b.time else a.value


def _is_numeric(v: Any) -> bool:
    """True for values that interpolate. ``bool`` deliberately does not:
    Python's ``bool ⊂ int`` would lerp what JS's ``typeof`` snaps."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)
