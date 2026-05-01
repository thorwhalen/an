"""Clip: a named bundle of channels with a duration and loop mode.

A clip is what you'd call an "animation" in Spine / Rive terminology — a
reusable unit (e.g. ``"walk_cycle"``, ``"wave"``). Evaluating a clip at time
``t`` produces a `Pose` by evaluating each of its channels at ``t``.

Loop modes:

- ``LoopMode.ONCE`` — past ``duration``, the last frame holds.
- ``LoopMode.LOOP`` — ``t`` wraps modulo ``duration``.
- ``LoopMode.PING_PONG`` — ``t`` ping-pongs over ``[0, duration]``.

>>> from an.adapters.cutout.channel import Channel, Keyframe
>>> ch = Channel("a", "x", [Keyframe(0.0, 0.0), Keyframe(1.0, 10.0)])
>>> clip = Clip("walk", duration=1.0, channels=[ch], loop_mode=LoopMode.LOOP)
>>> evaluate(clip, 0.5)[("a", "x")]
5.0
>>> evaluate(clip, 1.25)[("a", "x")]  # loop wraps
2.5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from an.adapters.cutout.channel import Channel
from an.adapters.cutout.channel import evaluate as _evaluate_channel
from an.adapters.cutout.pose import Pose


class LoopMode(str, Enum):
    """How a clip behaves past its natural duration."""

    ONCE = "once"
    LOOP = "loop"
    PING_PONG = "ping_pong"


@dataclass(slots=True)
class Clip:
    """Named animation: a duration + a bundle of channels."""

    name: str
    duration: float
    channels: list[Channel] = field(default_factory=list)
    loop_mode: LoopMode = LoopMode.ONCE

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError(
                f"Clip {self.name!r} duration must be > 0; got {self.duration}"
            )


def _wrap_time(t: float, duration: float, loop_mode: LoopMode) -> float:
    """Apply the loop mode to ``t``, returning a time within ``[0, duration]``."""
    if t < 0:
        return 0.0
    if loop_mode == LoopMode.ONCE:
        return min(t, duration)
    if loop_mode == LoopMode.LOOP:
        return t % duration if duration > 0 else 0.0
    # PING_PONG: bounce between 0 and duration over period 2*duration
    period = 2.0 * duration
    phase = t % period
    return phase if phase <= duration else period - phase


def evaluate(clip: Clip, t: float) -> Pose:
    """Evaluate ``clip`` at time ``t``, returning a `Pose`."""
    wrapped = _wrap_time(t, clip.duration, clip.loop_mode)
    pose: Pose = {}
    for ch in clip.channels:
        pose[(ch.target, ch.property)] = _evaluate_channel(ch, wrapped)
    return pose
