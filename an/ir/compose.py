"""Composition combinators for authoring action trees, plus a flattener.

The composition tree is the *authoring* form. The flat list of `FlatAction`s
with absolute start times is the *canonical* form that gets verified, cached,
and rendered. Both forms round-trip through the schema; the flat form is
what tooling reasons about.

>>> from an.ir.compose import sequence, parallel, tween, delay, flatten
>>> action = sequence(
...     tween("charlie/torso", "rotation", to=10.0, duration=1.0),
...     delay(0.5),
...     tween("charlie/torso", "rotation", to=0.0, duration=1.0),
... )
>>> flat = flatten(action)
>>> [round(f.start, 3) for f in flat]
[0.0, 1.5]
>>> [round(f.end, 3) for f in flat]
[1.0, 2.5]
>>> action2 = parallel(
...     tween("a", "x", to=1.0, duration=2.0),
...     tween("b", "y", to=1.0, duration=3.0),
... )
>>> flat2 = flatten(action2)
>>> [(round(f.start, 2), round(f.end, 2)) for f in flat2]
[(0.0, 2.0), (0.0, 3.0)]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from an.base import EasingSpec, PathStr, Seconds
from an.ir.schema import (
    DFLT_EXPRESSION_BLEND_S,
    Action,
    DelayAction,
    ExpressionAction,
    LoopAction,
    ParallelAction,
    PlayAction,
    SequenceAction,
    SetAction,
    TweenAction,
)


# -----------------------------------------------------------------------------
# Atoms — small fluent constructors that produce schema instances directly.
# -----------------------------------------------------------------------------


def set_(target: PathStr, property: str, value: Any, *, at: Seconds = 0.0) -> SetAction:
    """Discrete property set at time ``at`` (relative to its enclosing scope)."""
    return SetAction(target=target, property=property, value=value, at=at)


def tween(
    target: PathStr,
    property: str,
    to: Any,
    duration: Seconds,
    *,
    from_: Any | None = None,
    easing: EasingSpec | None = "ease_in_out",
) -> TweenAction:
    """Animate a property from ``from_`` (or its current value) to ``to``."""
    return TweenAction(
        target=target,
        property=property,
        to_value=to,
        from_value=from_,
        duration=duration,
        easing=easing,
    )


def play(
    target: PathStr,
    animation: str,
    *,
    duration: Seconds | None = None,
    speed: float = 1.0,
    loop: bool | None = None,
) -> PlayAction:
    """Play a named animation of the target entity's descriptor (an#7).

    ``duration=None`` fills the animation's natural length — or the shot's
    remainder for a looping one — but counts as **zero** in a ``sequence``,
    so a sibling placed after it starts at the same instant:

    >>> [f.start for f in flatten(sequence(play("a", "idle_breath"), delay(1.0), play("a", "blink")))]
    [0.0, 1.0]
    >>> [f.start for f in flatten(sequence(play("a", "idle_breath", duration=2.0), play("a", "blink")))]
    [0.0, 2.0]
    """
    return PlayAction(
        target=target,
        animation=animation,
        duration=duration,
        speed=speed,
        loop=loop,
    )


def expression(
    target: PathStr,
    preset: str | None = None,
    *,
    axes: dict[str, float] | None = None,
    intensity: float = 1.0,
    duration: Seconds | None = None,
    blend: Seconds = DFLT_EXPRESSION_BLEND_S,
) -> ExpressionAction:
    """Hold a facial expression on an entity (an#98).

    ``duration=None`` runs to the shot end and counts as **zero** in a
    ``sequence``, like ``play``:

    >>> [f.start for f in flatten(sequence(expression("a", "happy"), delay(1.0), expression("a", "sad")))]
    [0.0, 1.0]
    >>> flatten(expression("a", "angry", duration=2.0))[0].end
    2.0
    """
    return ExpressionAction(
        target=target,
        preset=preset,
        axes=dict(axes or {}),
        intensity=intensity,
        duration=duration,
        blend=blend,
    )


# -----------------------------------------------------------------------------
# Combinators — build composition trees.
# -----------------------------------------------------------------------------


def sequence(*actions: Action) -> SequenceAction:
    """Run children one after the other. Total duration = sum of child durations."""
    return SequenceAction(children=list(actions))


def parallel(*actions: Action) -> ParallelAction:
    """Run all children at once. Total duration = max of child durations."""
    return ParallelAction(children=list(actions))


def delay(duration: Seconds) -> DelayAction:
    """An empty span that consumes time. Useful inside `sequence`."""
    return DelayAction(duration=duration)


def loop(action: Action, count: int) -> LoopAction:
    """Repeat ``action`` ``count`` times."""
    if count < 1:
        raise ValueError(f"loop count must be >= 1, got {count}")
    return LoopAction(child=action, count=count)


# -----------------------------------------------------------------------------
# Duration calculation — read-only walk over a composition tree.
# -----------------------------------------------------------------------------


def duration_of(action: Action) -> Seconds:
    """Compute the total duration of an action tree without evaluating it.

    >>> duration_of(tween("a", "x", to=1.0, duration=2.0))
    2.0
    >>> duration_of(sequence(delay(0.5), tween("a", "x", to=1.0, duration=1.5)))
    2.0
    >>> duration_of(parallel(delay(0.5), delay(2.5)))
    2.5
    >>> duration_of(loop(delay(0.25), 4))
    1.0
    >>> duration_of(set_("a", "x", 1.0))
    0.0
    """
    if isinstance(action, SetAction):
        return 0.0
    if isinstance(action, TweenAction):
        return action.duration
    if isinstance(action, (PlayAction, ExpressionAction)):
        return action.duration if action.duration is not None else 0.0
    if isinstance(action, DelayAction):
        return action.duration
    if isinstance(action, SequenceAction):
        return sum((duration_of(c) for c in action.children), 0.0)
    if isinstance(action, ParallelAction):
        return max((duration_of(c) for c in action.children), default=0.0)
    if isinstance(action, LoopAction):
        return duration_of(action.child) * action.count
    raise TypeError(f"Unknown action type: {type(action).__name__}")


# -----------------------------------------------------------------------------
# Flattening — produce the canonical-form list of FlatAction.
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlatAction:
    """A leaf action with its absolute start and end times.

    The flat-form list is the canonical representation passed to renderers
    and verifiers. Composition nodes (sequence/parallel/delay/loop) do not
    appear in the flat form — they're collapsed into time offsets.
    """

    start: Seconds
    end: Seconds
    action: (
        Action  # always a leaf: SetAction | TweenAction | PlayAction | ExpressionAction
    )


def flatten(action: Action, *, start: Seconds = 0.0) -> list[FlatAction]:
    """Walk a composition tree, emitting leaf actions with absolute times.

    Delays are absorbed into the timeline (they don't appear in the output).
    Loops are unrolled by simple repetition — appropriate at v0.1; the cutout
    runtime can re-roll for efficiency later.
    """
    out: list[FlatAction] = []
    _flatten_into(action, start, out)
    return out


def _flatten_into(action: Action, t: Seconds, out: list[FlatAction]) -> Seconds:
    """Append leaf actions to ``out`` and return the new cursor time."""
    if isinstance(action, SetAction):
        # `at` is relative to enclosing scope; absolute start is t + at.
        abs_t = t + action.at
        out.append(FlatAction(start=abs_t, end=abs_t, action=action))
        return t  # set actions do not advance the cursor
    if isinstance(action, TweenAction):
        out.append(FlatAction(start=t, end=t + action.duration, action=action))
        return t + action.duration
    if isinstance(action, (PlayAction, ExpressionAction)):
        d = action.duration if action.duration is not None else 0.0
        out.append(FlatAction(start=t, end=t + d, action=action))
        return t + d
    if isinstance(action, DelayAction):
        return t + action.duration
    if isinstance(action, SequenceAction):
        cursor = t
        for child in action.children:
            cursor = _flatten_into(child, cursor, out)
        return cursor
    if isinstance(action, ParallelAction):
        max_end = t
        for child in action.children:
            child_end = _flatten_into(child, t, out)
            if child_end > max_end:
                max_end = child_end
        return max_end
    if isinstance(action, LoopAction):
        cursor = t
        for _ in range(action.count):
            cursor = _flatten_into(action.child, cursor, out)
        return cursor
    raise TypeError(f"Unknown action type: {type(action).__name__}")
